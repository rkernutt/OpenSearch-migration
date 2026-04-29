#!/usr/bin/env python3
"""shadow_diff.py — query-parity cutover gate.

Runs a list of saved queries against both the source (OpenSearch /
Elasticsearch) and the destination (Elastic Cloud / Serverless) and decides
whether the destination is producing equivalent results.

Designed to be called immediately before a cutover: if exit code is 0, the
destination is safe to receive live traffic; non-zero means at least one
query drifted beyond tolerance.

Comparators (per query):
  * `count`   — `total.value` parity (with optional ``--count-tolerance``)
  * `topk-ids` — Jaccard / IoU of the top-K hit ``_id`` lists
  * `topk-hashes` — SHA-256 of canonicalised top-K hit ``_source`` blobs
                   (catches per-doc field drift even when IDs match)
  * `status`   — HTTP status parity (catches schema / mapping issues)

Inputs
------
- ``--queries-file PATH`` — JSON file with a list of query objects:
    [
      {"name": "users-active",
       "index": "users",
       "body": {"query": {"term": {"active": true}}, "size": 50}},
      ...
    ]
- ``--queries-dir PATH``  — directory of ``*.json`` files; each file's
  basename is the query name unless it contains an explicit ``"name"``.

Both can be combined; queries are deduped by name.

Exits
-----
  0  every query within tolerance
  2  configuration error (no queries, missing flags, bad JSON)
  3  transport / auth failure talking to source or destination
  4  one or more queries drifted (others may have passed; the report
     enumerates failures by name)

Reuses ``DestAuth``, ``_SESSION``, ``_redact_response_text``, the JSON-log
toggle, and the SigV4 helper from ``validate_migration.py``.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

_repo_root = Path(__file__).resolve().parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import bootstrap_env  # noqa: E402

bootstrap_env.load()

import validate_migration as _vm  # noqa: E402
from validate_migration import (  # noqa: E402
    _SESSION,
    _TIMEOUT_SEARCH,
    DestAuth,
    _cli_log,
    _redact_response_text,
    opensearch_auth_sigv4,
)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_TRANSPORT = 3
EXIT_DRIFT = 4


# ---------------------------------------------------------------------------
# Query loading
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Query:
    name: str
    index: str
    body: Dict[str, Any]
    params: Dict[str, str] = dataclasses.field(default_factory=dict)


def _load_query_file(path: Path) -> List[Query]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected list or object, got {type(raw).__name__}")
    out: List[Query] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{path}[{i}]: not an object")
        if "index" not in item:
            raise ValueError(f"{path}[{i}]: missing 'index'")
        if "body" not in item:
            raise ValueError(f"{path}[{i}]: missing 'body'")
        name = item.get("name") or f"{path.stem}-{i}"
        out.append(
            Query(
                name=str(name),
                index=str(item["index"]),
                body=item["body"],
                params={str(k): str(v) for k, v in (item.get("params") or {}).items()},
            )
        )
    return out


def _load_queries(queries_file: Optional[str], queries_dir: Optional[str]) -> List[Query]:
    by_name: Dict[str, Query] = {}
    if queries_file:
        for q in _load_query_file(Path(queries_file)):
            by_name[q.name] = q
    if queries_dir:
        d = Path(queries_dir)
        if not d.is_dir():
            raise ValueError(f"--queries-dir not a directory: {queries_dir}")
        for path in sorted(d.glob("*.json")):
            for q in _load_query_file(path):
                by_name.setdefault(q.name, q)
    return list(by_name.values())


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Endpoint:
    label: str
    host: str
    auth_callable: Any
    headers: Dict[str, str]
    basic: Optional[Tuple[str, str]]


def _post_search(ep: _Endpoint, q: Query) -> Tuple[int, Dict[str, Any], str]:
    url = ep.host.rstrip("/") + "/" + q.index.strip("/") + "/_search"
    if q.params:
        from urllib.parse import urlencode

        url += "?" + urlencode(q.params)
    headers = dict(ep.headers)
    headers.setdefault("Content-Type", "application/json")
    auth = ep.basic if ep.basic else (ep.auth_callable() if ep.auth_callable else None)
    resp = _SESSION.post(
        url,
        data=json.dumps(q.body).encode("utf-8"),
        headers=headers,
        auth=auth,
        timeout=_TIMEOUT_SEARCH,
    )
    text_preview = resp.text[:512] if resp.status_code >= 400 else ""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body, text_preview


# ---------------------------------------------------------------------------
# Comparators
# ---------------------------------------------------------------------------


def _hits_total(body: Dict[str, Any]) -> int:
    hits = body.get("hits") or {}
    total = hits.get("total")
    if isinstance(total, dict):
        return int(total.get("value", 0))
    if isinstance(total, int):
        return total
    return 0


def _top_ids(body: Dict[str, Any], k: int) -> List[str]:
    hits = (body.get("hits") or {}).get("hits") or []
    return [str(h.get("_id")) for h in hits[:k] if h.get("_id") is not None]


def _canonical_source_hash(source: Any) -> str:
    return hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _top_hashes(body: Dict[str, Any], k: int) -> Dict[str, str]:
    hits = (body.get("hits") or {}).get("hits") or []
    out: Dict[str, str] = {}
    for h in hits[:k]:
        _id = h.get("_id")
        src = h.get("_source")
        if _id is None or src is None:
            continue
        out[str(_id)] = _canonical_source_hash(src)
    return out


def _jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / max(1, len(sa | sb))


# ---------------------------------------------------------------------------
# Diff loop
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class QueryResult:
    name: str
    index: str
    passed: bool
    failures: List[str]
    metrics: Dict[str, Any]
    source_status: int
    dest_status: int
    detail: Optional[str] = None


def _diff_query(
    q: Query,
    src: _Endpoint,
    dst: _Endpoint,
    *,
    top_k: int,
    count_tolerance: float,
    topk_id_threshold: float,
    topk_hash_threshold: float,
    require_hashes: bool,
) -> QueryResult:
    failures: List[str] = []
    metrics: Dict[str, Any] = {}
    detail = None
    try:
        src_status, src_body, src_preview = _post_search(src, q)
        dst_status, dst_body, dst_preview = _post_search(dst, q)
    except requests.RequestException as e:
        return QueryResult(
            name=q.name,
            index=q.index,
            passed=False,
            failures=["transport"],
            metrics={},
            source_status=0,
            dest_status=0,
            detail=_redact_response_text(str(e)),
        )

    metrics["source_status"] = src_status
    metrics["dest_status"] = dst_status

    if src_status >= 400 or dst_status >= 400:
        # Status mismatch is an instant fail; record both.
        failures.append("status")
        if src_preview or dst_preview:
            detail = (
                f"src={src_status} {_redact_response_text(src_preview)} "
                f"| dst={dst_status} {_redact_response_text(dst_preview)}"
            )
        return QueryResult(
            q.name, q.index, False, failures, metrics, src_status, dst_status, detail
        )

    src_total = _hits_total(src_body)
    dst_total = _hits_total(dst_body)
    metrics["source_total"] = src_total
    metrics["dest_total"] = dst_total
    if src_total == 0 and dst_total == 0:
        delta = 0.0
    else:
        delta = abs(src_total - dst_total) / max(1, src_total)
    metrics["count_relative_delta"] = round(delta, 6)
    if delta > count_tolerance:
        failures.append(f"count: |Δ|={delta:.4f} > tol={count_tolerance}")

    src_ids = _top_ids(src_body, top_k)
    dst_ids = _top_ids(dst_body, top_k)
    metrics["topk_jaccard"] = round(_jaccard(src_ids, dst_ids), 6)
    metrics["source_top_ids"] = src_ids
    metrics["dest_top_ids"] = dst_ids
    if metrics["topk_jaccard"] < topk_id_threshold:
        failures.append(
            f"topk-ids: jaccard={metrics['topk_jaccard']:.3f} < threshold={topk_id_threshold}"
        )

    if require_hashes:
        src_hashes = _top_hashes(src_body, top_k)
        dst_hashes = _top_hashes(dst_body, top_k)
        # For IDs present in both, count how many have matching content hashes.
        common = set(src_hashes) & set(dst_hashes)
        if not common:
            metrics["topk_hash_overlap"] = 0.0
        else:
            matches = sum(1 for i in common if src_hashes[i] == dst_hashes[i])
            metrics["topk_hash_overlap"] = round(matches / len(common), 6)
        if metrics["topk_hash_overlap"] < topk_hash_threshold:
            failures.append(
                f"topk-hashes: match={metrics['topk_hash_overlap']:.3f} < threshold={topk_hash_threshold}"
            )

    passed = not failures
    return QueryResult(q.name, q.index, passed, failures, metrics, src_status, dst_status, detail)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Run a list of queries against the source and destination and report "
            "drift in count, top-K hit IDs, and (optionally) per-hit content hashes."
        ),
    )
    # source
    p.add_argument("--source-host", default=os.environ.get("SOURCE_OPENSEARCH_HOST"))
    p.add_argument("--source-user", default=os.environ.get("SOURCE_OPENSEARCH_USER"))
    p.add_argument("--source-password", default=os.environ.get("SOURCE_OPENSEARCH_PASSWORD"))
    p.add_argument("--source-region", default=os.environ.get("AWS_REGION", "us-east-1"))

    # dest
    p.add_argument("--dest-host", default=os.environ.get("DEST_ELASTIC_HOST"))
    p.add_argument("--dest-api-key", default=os.environ.get("DEST_ELASTIC_API_KEY"))
    p.add_argument(
        "--dest-api-key-encoded",
        action="store_true",
        help="Set if --dest-api-key is already Base64-encoded.",
    )
    p.add_argument("--dest-user", default=os.environ.get("DEST_ELASTIC_USER"))
    p.add_argument("--dest-password", default=os.environ.get("DEST_ELASTIC_PASSWORD"))

    # queries
    p.add_argument("--queries-file", default=None)
    p.add_argument("--queries-dir", default=None)

    # comparator knobs
    p.add_argument("--top-k", type=int, default=20)
    p.add_argument(
        "--count-tolerance",
        type=float,
        default=0.0,
        help="Allowed |Δcount| / source_count. 0.0 means exact parity.",
    )
    p.add_argument(
        "--topk-id-threshold",
        type=float,
        default=1.0,
        help="Minimum Jaccard overlap on top-K IDs (0..1). 1.0 means exact parity.",
    )
    p.add_argument(
        "--topk-hash-threshold",
        type=float,
        default=1.0,
        help="Minimum fraction of common-ID hits whose _source hash matches.",
    )
    p.add_argument(
        "--no-hashes",
        action="store_true",
        help="Skip per-hit _source hashing (just IDs and counts).",
    )

    # runtime
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--report", default=None, help="Optional path for full JSON report.")
    p.add_argument("--log-format", choices=("text", "json"), default="text")
    p.add_argument("--strict-exit-codes", action="store_true")
    return p.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> Optional[str]:
    if not args.source_host:
        return "--source-host (or SOURCE_OPENSEARCH_HOST) is required"
    if not args.dest_host:
        return "--dest-host (or DEST_ELASTIC_HOST) is required"
    if not (args.queries_file or args.queries_dir):
        return "set --queries-file and/or --queries-dir"
    if not (args.dest_api_key or (args.dest_user and args.dest_password)):
        return "set --dest-api-key (or DEST_ELASTIC_*) or --dest-user / --dest-password"
    if not 0 <= args.count_tolerance <= 10:
        return "--count-tolerance must be in [0, 10]"
    if not 0 <= args.topk_id_threshold <= 1:
        return "--topk-id-threshold must be in [0, 1]"
    if not 0 <= args.topk_hash_threshold <= 1:
        return "--topk-hash-threshold must be in [0, 1]"
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
        queries = _load_queries(args.queries_file, args.queries_dir)
    except (OSError, ValueError) as e:
        _cli_log("error", f"failed to load queries: {e}")
        return EXIT_CONFIG if args.strict_exit_codes else 1
    if not queries:
        _cli_log("error", "no queries loaded")
        return EXIT_CONFIG if args.strict_exit_codes else 1

    # source endpoint: SigV4 if no basic creds; basic otherwise
    use_sigv4 = not (args.source_user and args.source_password)
    src_basic = None if use_sigv4 else (args.source_user, args.source_password)
    src_auth_callable = (lambda: opensearch_auth_sigv4(args.source_region)) if use_sigv4 else None
    src = _Endpoint("source", args.source_host, src_auth_callable, {}, src_basic)

    dest_auth_holder = DestAuth(
        api_key=args.dest_api_key,
        api_key_encoded=args.dest_api_key_encoded,
        user=args.dest_user,
        password=args.dest_password,
    )
    dest_headers, dest_basic = dest_auth_holder.apply()
    dst = _Endpoint("dest", args.dest_host, None, dest_headers or {}, dest_basic)

    started = time.monotonic()
    results: List[QueryResult] = []
    transport_failures = 0

    def _run(q: Query) -> QueryResult:
        return _diff_query(
            q,
            src,
            dst,
            top_k=args.top_k,
            count_tolerance=args.count_tolerance,
            topk_id_threshold=args.topk_id_threshold,
            topk_hash_threshold=args.topk_hash_threshold,
            require_hashes=not args.no_hashes,
        )

    if args.workers <= 1:
        for q in queries:
            results.append(_run(q))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(_run, q): q for q in queries}
            for fut in as_completed(futures):
                results.append(fut.result())

    drifts = [r for r in results if not r.passed]
    for r in drifts:
        if r.failures == ["transport"]:
            transport_failures += 1

    summary: Dict[str, Any] = {
        "queries_total": len(results),
        "queries_drifted": len(drifts),
        "queries_passed": len(results) - len(drifts),
        "transport_failures": transport_failures,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "drift_by_name": [
            {
                "name": r.name,
                "index": r.index,
                "failures": r.failures,
                "metrics": r.metrics,
                "detail": r.detail,
            }
            for r in drifts
        ],
    }

    for r in results:
        status = "pass" if r.passed else "drift"
        _cli_log(
            "info" if r.passed else "warning",
            f"{r.name} {status}",
            failures=r.failures,
            metrics=r.metrics if not r.passed else None,
        )
    if args.log_format == "json":
        print(json.dumps(summary))

    if args.report:
        full = {
            "summary": summary,
            "queries": [
                {
                    "name": r.name,
                    "index": r.index,
                    "passed": r.passed,
                    "failures": r.failures,
                    "metrics": r.metrics,
                    "source_status": r.source_status,
                    "dest_status": r.dest_status,
                    "detail": r.detail,
                }
                for r in results
            ],
        }
        Path(args.report).write_text(json.dumps(full, indent=2) + "\n", encoding="utf-8")

    if transport_failures and transport_failures == len(results):
        return EXIT_TRANSPORT if args.strict_exit_codes else 1
    if drifts:
        return EXIT_DRIFT if args.strict_exit_codes else 1
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
