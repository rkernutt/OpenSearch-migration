"""replayer.py — replay captured proxy traffic against a destination.

Reads NDJSON capture files produced by :mod:`Proxy.capture` (either
local files or gzipped objects under an ``s3://`` prefix), reissues each
request against the destination, and optionally compares the response
to the captured original.

Comparators:
  * ``status`` — captured vs replayed HTTP status.
  * ``hash``   — SHA-256 of canonicalised JSON body. JSON is parsed and
                 sorted before hashing so cosmetic key-order differences
                 don't trigger false drift.
  * ``size``   — response byte length parity within tolerance.

Filters:
  * ``--method GET,POST``        — only these methods.
  * ``--path-include REGEX``     — only paths matching (repeatable).
  * ``--path-exclude REGEX``     — drop paths matching (repeatable).
  * ``--since/--until ISO8601``  — time-bounded replay.
  * ``--max-requests N``         — sampled replay.

Tuning:
  * ``--workers N``         — parallel replay (default 4).
  * ``--rate-limit RPS``    — global request-per-second cap (token bucket).
  * ``--timeout-seconds T`` — per-request timeout.

Exits (with ``--strict-exit-codes``):
  0  every replayed request matched the comparator thresholds
  2  configuration error
  3  transport / auth failure dominant (every replayed request errored)
  4  one or more comparator drifts; rest of the report enumerates
"""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

import requests

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import bootstrap_env  # noqa: E402

bootstrap_env.load()

import validate_migration as _vm  # noqa: E402
from validate_migration import (  # noqa: E402
    _SESSION,
    DestAuth,
    _cli_log,
    _redact_response_text,
)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_TRANSPORT = 3
EXIT_DRIFT = 4


# ---------------------------------------------------------------------------
# Capture record reader (local + S3)
# ---------------------------------------------------------------------------


def _open_local(path: Path) -> Iterator[Dict[str, Any]]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
    else:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


def _list_s3_objects(s3_uri: str, region: str) -> List[Tuple[str, str]]:
    import boto3  # type: ignore[import-not-found]

    u = urlparse(s3_uri)
    if u.scheme != "s3" or not u.netloc:
        raise ValueError(f"--captures uri invalid: {s3_uri!r}")
    bucket = u.netloc
    prefix = u.path.lstrip("/")
    client = boto3.client("s3", region_name=region)
    paginator = client.get_paginator("list_objects_v2")
    out: List[Tuple[str, str]] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            if key.endswith(".ndjson") or key.endswith(".ndjson.gz"):
                out.append((bucket, key))
    return out


def _open_s3(bucket: str, key: str, region: str) -> Iterator[Dict[str, Any]]:
    import boto3  # type: ignore[import-not-found]

    client = boto3.client("s3", region_name=region)
    body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    if key.endswith(".gz"):
        body = gzip.decompress(body)
    f = io.StringIO(body.decode("utf-8"))
    for line in f:
        line = line.strip()
        if line:
            yield json.loads(line)


def _load_captures(uri: str, region: str) -> Iterator[Dict[str, Any]]:
    if uri.startswith("s3://"):
        for bucket, key in _list_s3_objects(uri, region):
            yield from _open_s3(bucket, key, region)
        return
    p = Path(uri)
    if p.is_file():
        yield from _open_local(p)
        return
    if p.is_dir():
        for child in sorted(p.glob("**/*.ndjson*")):
            if child.is_file():
                yield from _open_local(child)
        return
    raise ValueError(f"--captures path not found: {uri}")


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Filters:
    methods: Optional[List[str]]
    include: List[re.Pattern]
    exclude: List[re.Pattern]
    since: Optional[datetime]
    until: Optional[datetime]
    max_requests: Optional[int]

    def keep(self, rec: Dict[str, Any]) -> bool:
        method = (rec.get("method") or "").upper()
        if self.methods and method not in self.methods:
            return False
        path = rec.get("path") or ""
        if self.include and not any(r.search(path) for r in self.include):
            return False
        if self.exclude and any(r.search(path) for r in self.exclude):
            return False
        ts = rec.get("ts")
        if (self.since or self.until) and ts:
            try:
                t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")
            except ValueError:
                t = None
            if t is not None:
                if self.since and t < self.since:
                    return False
                if self.until and t > self.until:
                    return False
        return True


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognised ISO 8601 timestamp: {s!r}")


# ---------------------------------------------------------------------------
# Replay + comparators
# ---------------------------------------------------------------------------


def _canonical_json_hash(text: str) -> Optional[str]:
    if not text:
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    canon = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canon).hexdigest()


@dataclasses.dataclass
class ReplayResult:
    method: str
    path: str
    captured_status: int
    replayed_status: int
    captured_bytes: int
    replayed_bytes: int
    captured_hash: Optional[str]
    replayed_hash: Optional[str]
    passed: bool
    failures: List[str]
    detail: Optional[str] = None


class _RateLimiter:
    """Simple token bucket. ``rate`` requests per second."""

    def __init__(self, rate: float) -> None:
        self.rate = max(0.0, rate)
        self._tokens = self.rate
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.rate <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.rate, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
            time.sleep(max(0.001, (1 - self._tokens) / max(self.rate, 1)))


def _replay_one(
    rec: Dict[str, Any],
    *,
    dest_host: str,
    dest_headers: Dict[str, str],
    dest_basic: Optional[Tuple[str, str]],
    timeout_seconds: float,
    size_tolerance: float,
    require_status_match: bool,
    require_hash_match: bool,
    rate_limiter: _RateLimiter,
) -> ReplayResult:
    method = (rec.get("method") or "").upper()
    path = rec.get("path") or "/"
    qs = rec.get("query_string") or ""
    body = rec.get("request_body")
    if body is None and rec.get("request_body_bytes", 0) > 0:
        # Body wasn't captured inline; we can't faithfully replay write traffic.
        # Skip it but record the failure so the operator sees the gap.
        return ReplayResult(
            method=method,
            path=path,
            captured_status=int(rec.get("response_status", 0)),
            replayed_status=0,
            captured_bytes=int(rec.get("response_body_bytes", 0)),
            replayed_bytes=0,
            captured_hash=rec.get("response_body_hash"),
            replayed_hash=None,
            passed=False,
            failures=["body-not-inlined"],
            detail="capture omitted body (above max_body_bytes); cannot replay this request faithfully",
        )

    url = dest_host.rstrip("/") + path
    if qs:
        url += "?" + qs
    headers = dict(dest_headers or {})
    headers.setdefault("Content-Type", "application/json")
    rate_limiter.acquire()
    try:
        resp = _SESSION.request(
            method,
            url,
            data=(body or "").encode("utf-8") if body else None,
            headers=headers,
            auth=dest_basic,
            timeout=timeout_seconds,
        )
    except requests.RequestException as e:
        return ReplayResult(
            method=method,
            path=path,
            captured_status=int(rec.get("response_status", 0)),
            replayed_status=0,
            captured_bytes=int(rec.get("response_body_bytes", 0)),
            replayed_bytes=0,
            captured_hash=rec.get("response_body_hash"),
            replayed_hash=None,
            passed=False,
            failures=["transport"],
            detail=_redact_response_text(str(e)),
        )

    captured_status = int(rec.get("response_status", 0))
    captured_bytes = int(rec.get("response_body_bytes", 0))
    replayed_status = resp.status_code
    replayed_text = resp.text
    replayed_bytes = len(resp.content)
    captured_hash = rec.get("response_body_hash")
    replayed_hash = _canonical_json_hash(replayed_text)

    failures: List[str] = []
    if require_status_match and replayed_status != captured_status:
        failures.append(f"status: captured={captured_status} replayed={replayed_status}")
    if size_tolerance >= 0 and captured_bytes > 0:
        delta = abs(captured_bytes - replayed_bytes) / max(1, captured_bytes)
        if delta > size_tolerance:
            failures.append(
                f"size: |Δ|={delta:.4f} > tol={size_tolerance} (captured={captured_bytes} replayed={replayed_bytes})"
            )
    if require_hash_match and captured_hash and replayed_hash and captured_hash != replayed_hash:
        failures.append("hash: canonical JSON body differs")

    return ReplayResult(
        method=method,
        path=path,
        captured_status=captured_status,
        replayed_status=replayed_status,
        captured_bytes=captured_bytes,
        replayed_bytes=replayed_bytes,
        captured_hash=captured_hash,
        replayed_hash=replayed_hash,
        passed=not failures,
        failures=failures,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Replay captured proxy traffic against a destination Elasticsearch / "
            "Elastic Cloud cluster. Captured records can be local NDJSON files, a "
            "directory of NDJSON(.gz), or an s3:// prefix."
        ),
    )
    p.add_argument(
        "--captures",
        required=False,
        default=os.environ.get("PROXY_CAPTURE_DIR") or os.environ.get("PROXY_CAPTURE_S3_URI"),
        help="Path or s3:// URI to read capture NDJSON from (defaults to the proxy's capture target).",
    )
    p.add_argument("--captures-region", default=os.environ.get("AWS_REGION", "us-east-1"))

    # destination
    p.add_argument("--dest-host", default=os.environ.get("DEST_ELASTIC_HOST"))
    p.add_argument("--dest-api-key", default=os.environ.get("DEST_ELASTIC_API_KEY"))
    p.add_argument("--dest-api-key-encoded", action="store_true")
    p.add_argument("--dest-user", default=os.environ.get("DEST_ELASTIC_USER"))
    p.add_argument("--dest-password", default=os.environ.get("DEST_ELASTIC_PASSWORD"))

    # filtering
    p.add_argument("--method", default="GET,POST", help="Comma-separated allowed methods.")
    p.add_argument("--path-include", action="append", default=[])
    p.add_argument("--path-exclude", action="append", default=[])
    p.add_argument("--since", default=None, help="ISO 8601 timestamp (UTC).")
    p.add_argument("--until", default=None, help="ISO 8601 timestamp (UTC).")
    p.add_argument("--max-requests", type=int, default=None)

    # comparator knobs
    p.add_argument("--no-status-check", action="store_true")
    p.add_argument("--no-hash-check", action="store_true")
    p.add_argument(
        "--size-tolerance",
        type=float,
        default=0.10,
        help="Allowed |Δsize| / captured_size. Default 0.10 (10%%). Set <0 to disable.",
    )

    # runtime
    p.add_argument("--workers", type=int, default=4)
    p.add_argument(
        "--rate-limit", type=float, default=0.0, help="Requests per second (0=unbounded)."
    )
    p.add_argument("--timeout-seconds", type=float, default=30.0)
    p.add_argument("--report", default=None, help="Optional path for full JSON report.")
    p.add_argument("--log-format", choices=("text", "json"), default="text")
    p.add_argument("--strict-exit-codes", action="store_true")

    return p.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> Optional[str]:
    if not args.captures:
        return "--captures (or PROXY_CAPTURE_DIR / PROXY_CAPTURE_S3_URI) is required"
    if not args.dest_host:
        return "--dest-host (or DEST_ELASTIC_HOST) is required"
    if not (args.dest_api_key or (args.dest_user and args.dest_password)):
        return "set --dest-api-key (or DEST_ELASTIC_*) or --dest-user / --dest-password"
    if args.size_tolerance > 10:
        return "--size-tolerance is unreasonably large; expected something under 1.0"
    if args.workers < 1:
        return "--workers must be >= 1"
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    _vm._LOG_FORMAT_JSON = args.log_format == "json"

    cfg_err = _validate_args(args)
    if cfg_err:
        _cli_log("error", cfg_err)
        return EXIT_CONFIG if args.strict_exit_codes else 1

    try:
        since = _parse_iso(args.since)
        until = _parse_iso(args.until)
    except ValueError as e:
        _cli_log("error", str(e))
        return EXIT_CONFIG if args.strict_exit_codes else 1

    filters = _Filters(
        methods=[m.strip().upper() for m in args.method.split(",") if m.strip()] or None,
        include=[re.compile(p) for p in args.path_include],
        exclude=[re.compile(p) for p in args.path_exclude],
        since=since,
        until=until,
        max_requests=args.max_requests,
    )

    dest_auth = DestAuth(
        api_key=args.dest_api_key,
        api_key_encoded=args.dest_api_key_encoded,
        user=args.dest_user,
        password=args.dest_password,
    )
    dest_headers, dest_basic = dest_auth.apply()

    rate_limiter = _RateLimiter(args.rate_limit)

    started = time.monotonic()
    results: List[ReplayResult] = []

    def _replay(rec: Dict[str, Any]) -> ReplayResult:
        return _replay_one(
            rec,
            dest_host=args.dest_host,
            dest_headers=dest_headers or {},
            dest_basic=dest_basic,
            timeout_seconds=args.timeout_seconds,
            size_tolerance=args.size_tolerance,
            require_status_match=not args.no_status_check,
            require_hash_match=not args.no_hash_check,
            rate_limiter=rate_limiter,
        )

    try:
        records = _load_captures(args.captures, args.captures_region)
    except (OSError, ValueError) as e:
        _cli_log("error", f"failed to load captures: {e}")
        return EXIT_CONFIG if args.strict_exit_codes else 1

    selected: List[Dict[str, Any]] = []
    seen = 0
    for rec in records:
        seen += 1
        if not filters.keep(rec):
            continue
        selected.append(rec)
        if filters.max_requests is not None and len(selected) >= filters.max_requests:
            break

    if not selected:
        _cli_log("warning", "no records matched filters", seen=seen)
        if args.log_format == "json":
            print(
                json.dumps({"requests_total": 0, "requests_drifted": 0, "matched": 0, "seen": seen})
            )
        return EXIT_OK

    if args.workers <= 1:
        for rec in selected:
            results.append(_replay(rec))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(_replay, rec): rec for rec in selected}
            for fut in as_completed(futures):
                results.append(fut.result())

    drifts = [r for r in results if not r.passed]
    transport = [r for r in drifts if r.failures == ["transport"]]

    summary = {
        "requests_total": len(results),
        "requests_passed": len(results) - len(drifts),
        "requests_drifted": len(drifts),
        "transport_failures": len(transport),
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "matched": len(selected),
        "seen": seen,
        "drift_sample": [
            {
                "method": r.method,
                "path": r.path,
                "failures": r.failures,
                "captured_status": r.captured_status,
                "replayed_status": r.replayed_status,
                "captured_bytes": r.captured_bytes,
                "replayed_bytes": r.replayed_bytes,
                "detail": r.detail,
            }
            for r in drifts[:50]
        ],
    }

    for r in results:
        _cli_log(
            "info" if r.passed else "warning",
            f"{r.method} {r.path} {'pass' if r.passed else 'drift'}",
            failures=r.failures or None,
        )
    if args.log_format == "json":
        print(json.dumps(summary))

    if args.report:
        Path(args.report).write_text(
            json.dumps(
                {
                    "summary": summary,
                    "results": [dataclasses.asdict(r) for r in results],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    if results and len(transport) == len(results):
        return EXIT_TRANSPORT if args.strict_exit_codes else 1
    if drifts:
        return EXIT_DRIFT if args.strict_exit_codes else 1
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
