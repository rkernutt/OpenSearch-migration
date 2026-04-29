#!/usr/bin/env python3
"""Extract OpenSearch indices to gzipped NDJSON parts in S3.

Pairs with ``s3_bulk_load.py`` to form a complete S3-staged migration path:

    OpenSearch  --(s3_extract)-->  S3 NDJSON.gz + manifest  --(s3_bulk_load)-->  Elasticsearch

For each requested index the extractor:

  1. Reads the source ``_count`` (recorded in the manifest for reconciliation).
  2. Runs **sliced scroll** searches in parallel (``--slices``).
  3. Writes the results as **bulk-format NDJSON** (alternating action / source
     lines) into local gzipped tempfiles, rotated by ``--part-size-mb``.
  4. Uploads each rotated part to S3 with multipart upload.
  5. Updates ``_manifest.json`` after each part and after each completed slice.

Auth modes (mirroring ``preflight.py`` / ``validate_migration.py``):

  * **Basic auth** — pass ``--source-user`` / ``--source-password`` (also reads
    ``SOURCE_OPENSEARCH_USER`` / ``SOURCE_OPENSEARCH_PASSWORD`` env vars). Use
    this for fine-grained-access OpenSearch and **for routing through the
    in-repo SigV4 proxy**: just point ``--source-host`` at the proxy URL with
    its inbound basic-auth credentials and the proxy re-signs to OpenSearch.
  * **SigV4** — when no basic credentials are supplied, the extractor signs
    requests with the AWS provider chain (``--source-region``).

Resume model (v1):

  * Slice IDs are deterministic ``0..--slices-1``. The checkpoint file records
    completed ``"<index>::<slice>"`` keys; subsequent runs skip them.
  * If you change ``--slices`` between runs, delete the checkpoint and start
    again — slice numbering must be stable across resumes.

Exit codes (with ``--strict-exit-codes``):
  0  every requested index extracted; manifest written
  2  configuration error (missing flags, invalid S3 URI, bad query file)
  3  transport / auth failure talking to S3 or OpenSearch
  4  one or more indices failed during scroll (manifest still reflects what
     completed; rerun to retry)
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import bootstrap_env  # noqa: E402

bootstrap_env.load()

import validate_migration as _vm  # noqa: E402
from s3_migration.s3_common import (  # noqa: E402
    DATA_PREFIX,
    DEFAULT_PART_SUFFIX,
    IndexEntry,
    Manifest,
    PartEntry,
    S3Uri,
    load_checkpoint,
    make_s3_client,
    save_checkpoint,
    save_manifest,
    utc_now_iso,
)
from validate_migration import (  # noqa: E402
    _SESSION,
    _TIMEOUT_SEARCH,
    _TIMEOUT_SHORT,
    _cli_log,
    _redact_response_text,
    opensearch_auth_sigv4,
    validate_index_name,
)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_TRANSPORT = 3
EXIT_INDEX_FAILURES = 4

DEFAULT_SLICES = 4
DEFAULT_PAGE_SIZE = 1000
DEFAULT_PART_SIZE_MB = 64
DEFAULT_GZIP_LEVEL = 6
DEFAULT_SCROLL = "10m"


# ---------------------------------------------------------------------------
# Auth abstraction (sigv4 vs basic; proxy = basic against the proxy URL)
# ---------------------------------------------------------------------------


@dataclass
class SourceAuth:
    """Holds whichever auth flavour we're using to talk to OpenSearch."""

    use_sigv4: bool
    region: str = "us-east-1"
    user: Optional[str] = None
    password: Optional[str] = None
    auth_label: str = "basic"  # for manifest/audit; one of {sigv4, basic, proxy}

    def request_auth(self) -> Any:
        """Return the ``auth`` argument for `_SESSION.request(...)`."""
        if self.use_sigv4:
            return opensearch_auth_sigv4(self.region)
        if self.user is None or self.password is None:
            raise RuntimeError("basic auth requires --source-user and --source-password")
        return (self.user, self.password)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract OpenSearch indices to gzipped NDJSON parts in S3.",
    )
    p.add_argument(
        "--source-host",
        default=os.environ.get("SOURCE_OPENSEARCH_HOST"),
        help=(
            "OpenSearch base URL (env: SOURCE_OPENSEARCH_HOST). To go via the "
            "in-repo SigV4 proxy, set this to the proxy URL and pass basic-auth "
            "credentials matching its PROXY_USER / PROXY_PASSWORD."
        ),
    )
    p.add_argument(
        "--source-user",
        default=os.environ.get("SOURCE_OPENSEARCH_USER"),
        help="OpenSearch basic-auth user (env: SOURCE_OPENSEARCH_USER).",
    )
    p.add_argument(
        "--source-password",
        default=os.environ.get("SOURCE_OPENSEARCH_PASSWORD"),
        help="OpenSearch basic-auth password (env: SOURCE_OPENSEARCH_PASSWORD).",
    )
    p.add_argument(
        "--source-region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS region for SigV4 signing when basic auth is not supplied.",
    )
    p.add_argument(
        "--via-proxy",
        action="store_true",
        help=(
            "Informational: tag the manifest 'auth' field as 'proxy' instead of "
            "'basic'. Use when --source-host points at the SigV4 reverse proxy."
        ),
    )

    p.add_argument(
        "--indices",
        default=None,
        help="Comma-separated list of source index names.",
    )
    p.add_argument(
        "--indices-file",
        default=None,
        help="File with one source index name per line ('#' starts a comment).",
    )

    p.add_argument(
        "--s3-uri",
        required=True,
        help="s3://bucket/prefix/<job-id>/ — output prefix for parts and manifest.",
    )
    p.add_argument(
        "--job-id",
        default=None,
        help="Optional explicit job id. Default: inferred from --s3-uri or generated.",
    )

    p.add_argument(
        "--slices",
        type=int,
        default=DEFAULT_SLICES,
        help=f"Sliced scroll parallelism (default {DEFAULT_SLICES}).",
    )
    p.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Hits per scroll page (default {DEFAULT_PAGE_SIZE}).",
    )
    p.add_argument(
        "--scroll",
        default=DEFAULT_SCROLL,
        help=f"Scroll context lifetime (default {DEFAULT_SCROLL}).",
    )
    p.add_argument(
        "--part-size-mb",
        type=float,
        default=DEFAULT_PART_SIZE_MB,
        help=f"Rotate gzipped parts at ~this size in MB (default {DEFAULT_PART_SIZE_MB}).",
    )
    p.add_argument(
        "--gzip-level",
        type=int,
        default=DEFAULT_GZIP_LEVEL,
        choices=range(1, 10),
        help=f"Gzip compression level 1-9 (default {DEFAULT_GZIP_LEVEL}).",
    )
    p.add_argument(
        "--query-file",
        default=None,
        help="Optional JSON file with a query body (replaces match_all).",
    )
    p.add_argument(
        "--time-field",
        default=None,
        help="Time field for --since/--until range filter (e.g. @timestamp).",
    )
    p.add_argument("--since", default=None, help="Lower bound for --time-field (inclusive).")
    p.add_argument("--until", default=None, help="Upper bound for --time-field (exclusive).")

    p.add_argument(
        "--checkpoint-file",
        default=None,
        help="Local checkpoint path; resumes by skipping completed slices.",
    )
    p.add_argument("--dry-run", action="store_true", help="Plan only; do not extract or upload.")
    p.add_argument(
        "--aws-region",
        default=os.environ.get("AWS_REGION"),
        help="AWS region for the S3 client (default: provider chain).",
    )
    p.add_argument(
        "--s3-endpoint-url",
        default=os.environ.get("S3_ENDPOINT_URL"),
        help="Optional custom S3 endpoint (LocalStack / MinIO / VPC endpoint).",
    )
    p.add_argument("--log-format", choices=("text", "json"), default="text")
    p.add_argument("--strict-exit-codes", action="store_true")
    return p.parse_args(argv)


def _parse_indices(args: argparse.Namespace) -> List[str]:
    raw: List[str] = []
    if args.indices:
        raw.extend(s.strip() for s in args.indices.split(",") if s.strip())
    if args.indices_file:
        with open(args.indices_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    raw.append(line)
    seen: set = set()
    out: List[str] = []
    for name in raw:
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _validate_args(args: argparse.Namespace) -> Optional[str]:
    if not args.source_host:
        return "--source-host (or SOURCE_OPENSEARCH_HOST) is required"
    if not args.indices and not args.indices_file:
        return "set --indices or --indices-file"
    if args.slices < 1:
        return "--slices must be >= 1"
    if args.page_size <= 0:
        return "--page-size must be positive"
    if args.part_size_mb <= 0:
        return "--part-size-mb must be positive"
    if (args.since or args.until) and not args.time_field:
        return "--since/--until require --time-field"
    return None


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def _build_base_query(args: argparse.Namespace) -> Dict[str, Any]:
    if args.query_file:
        with open(args.query_file, encoding="utf-8") as f:
            body = json.load(f)
        if "query" not in body:
            raise ValueError("query file must contain a top-level 'query' key")
        base = body["query"]
    else:
        base = {"match_all": {}}

    if args.time_field and (args.since or args.until):
        rng: Dict[str, Any] = {}
        if args.since:
            rng["gte"] = args.since
        if args.until:
            rng["lt"] = args.until
        time_filter = {"range": {args.time_field: rng}}
        base = {"bool": {"must": [base], "filter": [time_filter]}}
    return base


# ---------------------------------------------------------------------------
# OpenSearch HTTP helpers (sigv4 / basic)
# ---------------------------------------------------------------------------


def _post_search(
    host: str,
    index: str,
    body: Dict[str, Any],
    auth: SourceAuth,
    scroll: str,
) -> Dict[str, Any]:
    url = f"{host.rstrip('/')}/{index}/_search?scroll={scroll}"
    resp = _SESSION.post(
        url,
        json=body,
        auth=auth.request_auth(),
        headers={"Content-Type": "application/json"},
        timeout=_TIMEOUT_SEARCH,
    )
    resp.raise_for_status()
    return resp.json()


def _post_scroll(host: str, scroll_id: str, auth: SourceAuth, scroll: str) -> Dict[str, Any]:
    url = f"{host.rstrip('/')}/_search/scroll"
    resp = _SESSION.post(
        url,
        json={"scroll": scroll, "scroll_id": scroll_id},
        auth=auth.request_auth(),
        headers={"Content-Type": "application/json"},
        timeout=_TIMEOUT_SEARCH,
    )
    resp.raise_for_status()
    return resp.json()


def _delete_scroll(host: str, scroll_id: str, auth: SourceAuth) -> None:
    """Clean up a scroll context; failure is non-fatal."""
    try:
        url = f"{host.rstrip('/')}/_search/scroll"
        _SESSION.delete(
            url,
            json={"scroll_id": scroll_id},
            auth=auth.request_auth(),
            headers={"Content-Type": "application/json"},
            timeout=_TIMEOUT_SHORT,
        )
    except requests.RequestException:
        pass


def _get_count(host: str, index: str, body: Dict[str, Any], auth: SourceAuth) -> int:
    url = f"{host.rstrip('/')}/{index}/_count"
    resp = _SESSION.post(
        url,
        json={"query": body},
        auth=auth.request_auth(),
        headers={"Content-Type": "application/json"},
        timeout=_TIMEOUT_SHORT,
    )
    resp.raise_for_status()
    return int(resp.json().get("count", 0))


# ---------------------------------------------------------------------------
# Hits → bulk-format NDJSON line bytes
# ---------------------------------------------------------------------------


def _hits_to_bulk_lines(hits: List[Dict[str, Any]], target_index: str) -> Iterator[bytes]:
    """Yield action+source line-bytes (no trailing newline) for each hit."""
    for hit in hits:
        action = {"index": {"_index": target_index, "_id": hit["_id"]}}
        yield json.dumps(action, separators=(",", ":")).encode("utf-8")
        # ``_source`` may be missing for `_source: false` queries; fall back to {}.
        source = hit.get("_source", {})
        yield json.dumps(source, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# Per-slice writer: gzip tempfile rotated on size, uploaded to S3
# ---------------------------------------------------------------------------


@dataclass
class SliceWriterConfig:
    s3: Any
    bucket: str
    job_key_prefix: str  # the "key" portion of job_uri ending with '/'
    index: str
    slice_id: int
    target_part_bytes: int
    gzip_level: int


class _SliceWriter:
    """Writes alternating action/source lines into gzipped tempfiles, uploads on rotate."""

    def __init__(self, cfg: SliceWriterConfig) -> None:
        self.cfg = cfg
        self._part_index = 0
        self._tmp: Optional[Any] = None
        self._gz: Optional[gzip.GzipFile] = None
        self._tmp_path: Optional[str] = None
        self._part_doc_count = 0
        self._uncompressed_bytes = 0
        self.parts: List[PartEntry] = []
        self.total_doc_count = 0

    # -- internal --------------------------------------------------------

    def _open_part(self) -> None:
        # delete=False so we control cleanup after upload
        self._tmp = tempfile.NamedTemporaryFile(
            prefix=f"s3extract-{self.cfg.index}-{self.cfg.slice_id}-",
            suffix=".ndjson.gz",
            delete=False,
        )
        self._tmp_path = self._tmp.name
        self._gz = gzip.GzipFile(fileobj=self._tmp, mode="wb", compresslevel=self.cfg.gzip_level)
        self._part_doc_count = 0
        self._uncompressed_bytes = 0

    def _close_and_upload(self) -> Optional[PartEntry]:
        if self._gz is None or self._tmp is None or self._tmp_path is None:
            return None
        self._gz.close()
        self._tmp.close()
        size_bytes = os.path.getsize(self._tmp_path)
        if self._part_doc_count == 0:
            os.unlink(self._tmp_path)
            self._tmp = self._gz = self._tmp_path = None
            return None

        rel_key = (
            f"{DATA_PREFIX}/{self.cfg.index}/"
            f"slice-{self.cfg.slice_id:03d}-part-{self._part_index:05d}{DEFAULT_PART_SUFFIX}"
        )
        full_key = self.cfg.job_key_prefix + rel_key
        try:
            self.cfg.s3.upload_file(self._tmp_path, self.cfg.bucket, full_key)
        finally:
            os.unlink(self._tmp_path)

        entry = PartEntry(
            key=rel_key,
            size_bytes=size_bytes,
            doc_count=self._part_doc_count,
            bulk_format=True,
        )
        self.parts.append(entry)
        self._part_index += 1
        self._tmp = self._gz = self._tmp_path = None
        return entry

    # -- public ----------------------------------------------------------

    def write_hits(self, hits: List[Dict[str, Any]]) -> None:
        if not hits:
            return
        if self._gz is None:
            self._open_part()
        assert self._gz is not None
        for line in _hits_to_bulk_lines(hits, self.cfg.index):
            self._gz.write(line)
            self._gz.write(b"\n")
            self._uncompressed_bytes += len(line) + 1
        self._part_doc_count += len(hits)
        self.total_doc_count += len(hits)
        # Rotate when the on-disk gzip size crosses the threshold. We flush
        # the GzipFile to get a meaningful os.path.getsize() reading.
        self._gz.flush()
        if self._tmp_path and os.path.getsize(self._tmp_path) >= self.cfg.target_part_bytes:
            self._close_and_upload()

    def finish(self) -> List[PartEntry]:
        self._close_and_upload()
        return self.parts


# ---------------------------------------------------------------------------
# Per-slice scroll loop
# ---------------------------------------------------------------------------


def _run_slice(
    *,
    s3: Any,
    job_uri: S3Uri,
    host: str,
    index: str,
    slice_id: int,
    slice_max: int,
    base_query: Dict[str, Any],
    page_size: int,
    scroll_window: str,
    auth: SourceAuth,
    target_part_bytes: int,
    gzip_level: int,
    dry_run: bool,
) -> Tuple[int, List[PartEntry]]:
    """Run one slice's scroll loop. Returns (doc_count, parts)."""
    body = {
        "size": page_size,
        "query": base_query,
        "sort": ["_doc"],
        "_source": True,
        "slice": {"id": slice_id, "max": slice_max} if slice_max > 1 else None,
    }
    if body["slice"] is None:
        body.pop("slice")

    if dry_run:
        return 0, []

    cfg = SliceWriterConfig(
        s3=s3,
        bucket=job_uri.bucket,
        job_key_prefix=(job_uri.key.rstrip("/") + "/") if job_uri.key else "",
        index=index,
        slice_id=slice_id,
        target_part_bytes=target_part_bytes,
        gzip_level=gzip_level,
    )
    writer = _SliceWriter(cfg)

    first = _post_search(host, index, body, auth, scroll_window)
    scroll_id: Optional[str] = first.get("_scroll_id")
    hits = first.get("hits", {}).get("hits", [])
    try:
        while hits:
            writer.write_hits(hits)
            if not scroll_id:
                break
            page = _post_scroll(host, scroll_id, auth, scroll_window)
            scroll_id = page.get("_scroll_id") or scroll_id
            hits = page.get("hits", {}).get("hits", [])
    finally:
        if scroll_id:
            _delete_scroll(host, scroll_id, auth)
        writer.finish()

    return writer.total_doc_count, writer.parts


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

    indices = _parse_indices(args)
    if not indices:
        _cli_log("error", "no indices supplied")
        return EXIT_CONFIG if args.strict_exit_codes else 1
    for name in indices:
        err = validate_index_name(name)
        if err:
            _cli_log("error", f"invalid index name: {err}")
            return EXIT_CONFIG if args.strict_exit_codes else 1

    try:
        job_uri = S3Uri.parse(args.s3_uri)
    except ValueError as e:
        _cli_log("error", str(e))
        return EXIT_CONFIG if args.strict_exit_codes else 1

    try:
        base_query = _build_base_query(args)
    except (OSError, ValueError) as e:
        _cli_log("error", f"invalid --query-file: {e}")
        return EXIT_CONFIG if args.strict_exit_codes else 1

    use_sigv4 = not (args.source_user and args.source_password)
    auth_label = "sigv4" if use_sigv4 else ("proxy" if args.via_proxy else "basic")
    source_auth = SourceAuth(
        use_sigv4=use_sigv4,
        region=args.source_region,
        user=args.source_user,
        password=args.source_password,
        auth_label=auth_label,
    )

    try:
        s3 = make_s3_client(region=args.aws_region, endpoint_url=args.s3_endpoint_url)
    except Exception as e:  # noqa: BLE001
        _cli_log("error", f"failed to create S3 client: {_redact_response_text(str(e))}")
        return EXIT_TRANSPORT if args.strict_exit_codes else 1

    job_id = args.job_id or _infer_job_id(job_uri)
    manifest = Manifest(
        job_id=job_id,
        created_at=utc_now_iso(),
        source={
            "host": args.source_host,
            "auth": auth_label,
            "region": args.source_region if use_sigv4 else None,
        },
        options={
            "scroll": args.scroll,
            "page_size": args.page_size,
            "slices": args.slices,
            "part_size_mb": args.part_size_mb,
            "time_field": args.time_field,
            "since": args.since,
            "until": args.until,
        },
    )

    ckpt = load_checkpoint(args.checkpoint_file or "")
    completed_slices: set = set(ckpt.get("completed_slices", []))

    target_part_bytes = int(args.part_size_mb * 1024 * 1024)
    failed_indices: List[str] = []
    manifest_lock = Lock()

    started_total = time.monotonic()
    for index in indices:
        ie = IndexEntry(name=index)
        manifest.indices.append(ie)

        # Source count (best-effort; non-fatal if it fails).
        try:
            ie.doc_count_source = _get_count(args.source_host, index, base_query, source_auth)
        except requests.RequestException as e:
            _cli_log(
                "warning",
                f"failed to read _count for {index}: {_redact_response_text(str(e))}",
            )

        if args.dry_run:
            _cli_log("info", f"[dry-run] would extract {index}", source_count=ie.doc_count_source)
            continue

        slice_max = max(1, args.slices)
        index_started = time.monotonic()
        index_failed = False

        with ThreadPoolExecutor(max_workers=slice_max) as pool:
            futures = {}
            for sid in range(slice_max):
                key = f"{index}::{sid}"
                if key in completed_slices:
                    _cli_log("info", f"skipping completed slice {key}")
                    continue
                futures[
                    pool.submit(
                        _run_slice,
                        s3=s3,
                        job_uri=job_uri,
                        host=args.source_host,
                        index=index,
                        slice_id=sid,
                        slice_max=slice_max,
                        base_query=base_query,
                        page_size=args.page_size,
                        scroll_window=args.scroll,
                        auth=source_auth,
                        target_part_bytes=target_part_bytes,
                        gzip_level=args.gzip_level,
                        dry_run=False,
                    )
                ] = sid

            for fut in as_completed(futures):
                sid = futures[fut]
                try:
                    _doc_count, parts = fut.result()
                except requests.RequestException as e:
                    _cli_log(
                        "error",
                        f"slice {index}::{sid} failed: {_redact_response_text(str(e))}",
                    )
                    index_failed = True
                    continue
                except Exception as e:  # noqa: BLE001
                    _cli_log("error", f"slice {index}::{sid} crashed: {e}")
                    index_failed = True
                    continue

                with manifest_lock:
                    ie.parts.extend(parts)
                    save_manifest(s3, job_uri, manifest)
                completed_slices.add(f"{index}::{sid}")
                if args.checkpoint_file:
                    save_checkpoint(
                        args.checkpoint_file,
                        {"completed_slices": sorted(completed_slices)},
                    )

        elapsed = time.monotonic() - index_started
        index_doc_count = sum(p.doc_count for p in ie.parts)
        _cli_log(
            "info",
            f"index {index} {'partial' if index_failed else 'done'}",
            seconds=round(elapsed, 1),
            parts=len(ie.parts),
            documents=index_doc_count,
        )
        if index_failed:
            failed_indices.append(index)

    # Final manifest write (also covers dry-run case)
    if not args.dry_run:
        try:
            save_manifest(s3, job_uri, manifest)
        except Exception as e:  # noqa: BLE001
            _cli_log("error", f"failed to write final manifest: {_redact_response_text(str(e))}")
            return EXIT_TRANSPORT if args.strict_exit_codes else 1

    summary = {
        "ok": not failed_indices,
        "indices_total": len(indices),
        "indices_failed": failed_indices,
        "documents_extracted": sum(sum(p.doc_count for p in ie.parts) for ie in manifest.indices),
        "parts_total": sum(len(ie.parts) for ie in manifest.indices),
        "elapsed_seconds": round(time.monotonic() - started_total, 1),
        "manifest_uri": str(job_uri.join("_manifest.json")),
        "dry_run": args.dry_run,
    }
    if args.log_format == "json":
        print(json.dumps(summary))
    else:
        _cli_log("info", "extract complete", **{k: v for k, v in summary.items() if k != "ok"})

    if failed_indices:
        return EXIT_INDEX_FAILURES if args.strict_exit_codes else 1
    return EXIT_OK


def _infer_job_id(job_uri: S3Uri) -> str:
    if job_uri.key:
        last = job_uri.key.rstrip("/").rsplit("/", 1)[-1]
        if last:
            return last
    return time.strftime("job-%Y%m%dT%H%M%SZ", time.gmtime())


if __name__ == "__main__":
    raise SystemExit(main())
