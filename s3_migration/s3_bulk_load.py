#!/usr/bin/env python3
"""Bulk-load gzipped NDJSON parts from S3 into Elasticsearch.

Reads a manifest written by ``s3_extract.py`` (or any S3 prefix containing
``*.ndjson`` / ``*.ndjson.gz`` files), streams each part, batches into
``_bulk`` requests with retries (via the shared session in
``validate_migration``), and writes per-document failures to a dead-letter
S3 prefix.

Resumable via a local checkpoint file: each successfully completed part is
recorded; subsequent runs skip those parts.

Reuses ``DestAuth``, ``_SESSION``, ``_redact_response_text``, and the
``_LOG_FORMAT_JSON`` toggle from ``validate_migration.py``. Auth/redaction
logic is *not* duplicated here.

Exit codes (with ``--strict-exit-codes``):
  0  every part loaded; no document-level failures
  2  configuration error (missing flags, invalid S3 URI, unreadable manifest)
  3  transport / auth failure communicating with S3 or Elasticsearch
  4  document-level failures (loader completed but at least one item failed;
     failures are in the DLQ unless ``--no-dlq`` was set, in which case the
     loader aborts)

Without ``--strict-exit-codes`` the loader uses ``0`` for "loaded with DLQ
handling all failures" and ``1`` for any other failure.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# Allow execution as `python s3_migration/s3_bulk_load.py` from the repo root,
# in addition to `python -m s3_migration.s3_bulk_load`.
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import bootstrap_env  # noqa: E402  (path bootstrap above)

bootstrap_env.load()

import validate_migration as _vm  # noqa: E402
from s3_migration.s3_common import (  # noqa: E402
    S3Uri,
    batch_bulk_pairs,
    detect_bulk_format,
    list_ndjson_parts,
    load_checkpoint,
    load_manifest,
    make_s3_client,
    open_ndjson_stream,
    resolve_manifest_part_key,
    save_checkpoint,
    serialise_bulk_body,
    to_bulk_pairs,
)
from validate_migration import (  # noqa: E402
    _SESSION,
    _TIMEOUT_SEARCH,
    DestAuth,
    _cli_log,
    _redact_response_text,
)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_TRANSPORT = 3
EXIT_DOC_ERRORS = 4


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Bulk-load gzipped NDJSON from S3 into Elasticsearch.",
    )
    p.add_argument(
        "--s3-uri",
        required=True,
        help=(
            "s3://bucket/prefix/job-id/ — directory of NDJSON parts (with optional _manifest.json)."
        ),
    )
    p.add_argument(
        "--dest-host",
        default=os.environ.get("DEST_ELASTIC_HOST"),
        help="Elasticsearch base URL (env: DEST_ELASTIC_HOST).",
    )
    p.add_argument(
        "--dest-api-key",
        default=os.environ.get("DEST_ELASTIC_API_KEY"),
        help="Elastic API key (env: DEST_ELASTIC_API_KEY).",
    )
    p.add_argument(
        "--dest-api-key-encoded",
        action="store_true",
        help="Set if --dest-api-key is already Base64-encoded.",
    )
    p.add_argument(
        "--dest-user",
        default=os.environ.get("DEST_ELASTIC_USER"),
        help="Elastic basic-auth user (env: DEST_ELASTIC_USER).",
    )
    p.add_argument(
        "--dest-password",
        default=os.environ.get("DEST_ELASTIC_PASSWORD"),
        help="Elastic basic-auth password (env: DEST_ELASTIC_PASSWORD).",
    )

    p.add_argument(
        "--target-index",
        default=None,
        help=(
            "Override destination index for source-only NDJSON (lines without bulk "
            "action headers). Ignored for bulk-format inputs."
        ),
    )
    p.add_argument(
        "--batch-size-mb",
        type=float,
        default=5.0,
        help="Maximum bulk request body size in MB (default 5; ES Cloud max ~15).",
    )
    p.add_argument(
        "--batch-max-items",
        type=int,
        default=5000,
        help="Maximum action+doc pairs per bulk request (default 5000).",
    )
    p.add_argument(
        "--max-in-flight",
        type=int,
        default=4,
        help="Maximum number of parts loaded concurrently (default 4).",
    )
    p.add_argument(
        "--dlq-prefix",
        default=None,
        help=("s3://bucket/prefix/ for per-document failures. Defaults to '<--s3-uri>/dlq/'."),
    )
    p.add_argument(
        "--no-dlq",
        action="store_true",
        help="Disable DLQ; any document-level failure aborts the part.",
    )
    p.add_argument(
        "--checkpoint-file",
        default=None,
        help="Local checkpoint path; resumes from the last completed part.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Read parts and validate format; do not POST to Elasticsearch.",
    )
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
    p.add_argument(
        "--log-format",
        choices=("text", "json"),
        default="text",
    )
    p.add_argument(
        "--strict-exit-codes",
        action="store_true",
        help="Use distinct exit codes for config (2), transport (3), doc errors (4).",
    )
    return p.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> Optional[str]:
    if not args.dest_host and not args.dry_run:
        return "--dest-host (or DEST_ELASTIC_HOST) is required"
    if not args.dry_run and not (args.dest_api_key or (args.dest_user and args.dest_password)):
        return "set --dest-api-key (or DEST_ELASTIC_*) or --dest-user / --dest-password"
    if args.batch_size_mb <= 0:
        return "--batch-size-mb must be positive"
    if args.batch_max_items <= 0:
        return "--batch-max-items must be positive"
    if args.max_in_flight <= 0:
        return "--max-in-flight must be positive"
    return None


# ---------------------------------------------------------------------------
# Bulk POST + response handling
# ---------------------------------------------------------------------------


def _post_bulk(
    host: str,
    dest_auth: DestAuth,
    body: bytes,
    timeout: int,
) -> requests.Response:
    headers, auth = dest_auth.apply({"Content-Type": "application/x-ndjson"})
    url = host.rstrip("/") + "/_bulk"
    return _SESSION.post(url, data=body, headers=headers, auth=auth, timeout=timeout)


def _process_bulk_response(
    resp: requests.Response,
    batch: List[Tuple[bytes, bytes, Dict[str, Any]]],
) -> Tuple[int, int, List[Dict[str, Any]]]:
    """Return ``(success_count, error_count, errors)`` from a `_bulk` response.

    For 5xx / 408 / 429 we raise via ``raise_for_status()`` so the caller can
    mark the part as a transport failure. For 200 with per-document errors,
    failures are returned in ``errors`` for DLQ handling.
    """
    if resp.status_code >= 500 or resp.status_code in (408, 429):
        resp.raise_for_status()
    if resp.status_code >= 400:
        # 4xx that isn't per-document — treat as transport-style abort.
        resp.raise_for_status()

    data = resp.json()
    if not data.get("errors"):
        return len(batch), 0, []

    items = data.get("items", [])
    errors: List[Dict[str, Any]] = []
    success = 0
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not item:
            continue
        op = next(iter(item))
        result = item[op]
        status = int(result.get("status", 0))
        if 200 <= status < 300:
            success += 1
            continue
        if i < len(batch):
            action_bytes, source_bytes, parsed_action = batch[i]
            errors.append(
                {
                    "status": status,
                    "error": result.get("error", {}),
                    "action": parsed_action,
                    "source": source_bytes.decode("utf-8", errors="replace"),
                }
            )
    return success, len(errors), errors


# ---------------------------------------------------------------------------
# DLQ
# ---------------------------------------------------------------------------


def _format_dlq_lines(errors: List[Dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for err in errors:
            gz.write(json.dumps(err, ensure_ascii=False).encode("utf-8"))
            gz.write(b"\n")
    return buf.getvalue()


def _dlq_target(dlq_uri: S3Uri, part_relname: str) -> S3Uri:
    if part_relname.endswith(".ndjson.gz"):
        out = part_relname[: -len(".ndjson.gz")] + "__failed.ndjson.gz"
    elif part_relname.endswith(".ndjson"):
        out = part_relname[: -len(".ndjson")] + "__failed.ndjson.gz"
    else:
        out = part_relname + "__failed.ndjson.gz"
    return dlq_uri.join(out)


def _write_dlq(
    s3: Any,
    dlq_uri: S3Uri,
    part_relname: str,
    errors: List[Dict[str, Any]],
) -> S3Uri:
    target = _dlq_target(dlq_uri, part_relname)
    s3.put_object(
        Bucket=target.bucket,
        Key=target.key,
        Body=_format_dlq_lines(errors),
        ContentType="application/gzip",
    )
    return target


# ---------------------------------------------------------------------------
# Per-part loader (executed on the worker thread pool)
# ---------------------------------------------------------------------------


def _load_part(
    s3: Any,
    part_uri: S3Uri,
    *,
    dest_host: str,
    dest_auth: DestAuth,
    target_index_override: Optional[str],
    batch_size_bytes: int,
    batch_max_items: int,
    dlq_uri: Optional[S3Uri],
    dry_run: bool,
) -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "part_key": part_uri.key,
        "ok": True,
        "success": 0,
        "errors": 0,
        "bytes_posted": 0,
        "dlq_uri": None,
        "transport_error": None,
    }

    line_iter = open_ndjson_stream(s3, part_uri)

    first: Optional[bytes] = None
    while True:
        try:
            candidate = next(line_iter)
        except StopIteration:
            break
        if candidate.strip():
            first = candidate
            break
    if first is None:
        return stats  # empty part is a no-op

    bulk_format = detect_bulk_format(first)

    def _stream() -> Any:
        yield first
        yield from line_iter

    pairs = to_bulk_pairs(
        _stream(),
        bulk_format=bulk_format,
        target_index=None if bulk_format else target_index_override,
    )

    all_errors: List[Dict[str, Any]] = []
    try:
        for batch in batch_bulk_pairs(
            pairs,
            max_bytes=batch_size_bytes,
            max_items=batch_max_items,
        ):
            body = serialise_bulk_body(batch)
            if dry_run:
                stats["bytes_posted"] += len(body)
                stats["success"] += len(batch)
                continue
            try:
                resp = _post_bulk(dest_host, dest_auth, body, timeout=_TIMEOUT_SEARCH)
                success, errcount, errors = _process_bulk_response(resp, batch)
            except requests.RequestException as e:
                stats["ok"] = False
                stats["transport_error"] = _redact_response_text(str(e))
                return stats
            stats["success"] += success
            stats["errors"] += errcount
            stats["bytes_posted"] += len(body)
            if errors:
                all_errors.extend(errors)
    except ValueError as e:
        # Malformed bulk format — treat as a transport-class failure for the part.
        stats["ok"] = False
        stats["transport_error"] = f"format error: {e}"
        return stats

    if all_errors:
        if dlq_uri is None:
            stats["ok"] = False
            stats["transport_error"] = f"{len(all_errors)} document-level failures (DLQ disabled)"
        else:
            try:
                target = _write_dlq(s3, dlq_uri, _basename(part_uri.key), all_errors)
                stats["dlq_uri"] = str(target)
            except Exception as e:  # noqa: BLE001 - surface as transport
                stats["ok"] = False
                stats["transport_error"] = f"DLQ write failed: {_redact_response_text(str(e))}"
    return stats


def _basename(key: str) -> str:
    return key.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


class _BadManifestError(Exception):
    """Raised when an `_manifest.json` exists but cannot be parsed."""


def _resolve_part_list(s3: Any, job_uri: S3Uri) -> Tuple[List[S3Uri], bool]:
    """Return ``(parts, has_manifest)``.

    Prefers a manifest under ``job_uri``; falls back to listing
    ``*.ndjson(.gz)`` objects under the prefix. Raises `_BadManifestError`
    on a present-but-unparseable manifest (treated as a config error).
    """
    try:
        manifest = load_manifest(s3, job_uri)
    except ValueError as e:
        raise _BadManifestError(str(e)) from e
    if manifest is not None:
        parts: List[S3Uri] = []
        for ie in manifest.indices:
            for p in ie.parts:
                key = resolve_manifest_part_key(job_uri, p.key)
                parts.append(S3Uri(bucket=job_uri.bucket, key=key))
        return parts, True
    listed = list_ndjson_parts(s3, job_uri)
    return [u for u, _size in listed], False


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    _vm._LOG_FORMAT_JSON = args.log_format == "json"

    cfg_err = _validate_args(args)
    if cfg_err:
        _cli_log("error", cfg_err)
        return EXIT_CONFIG if args.strict_exit_codes else 1

    try:
        job_uri = S3Uri.parse(args.s3_uri)
    except ValueError as e:
        _cli_log("error", str(e))
        return EXIT_CONFIG if args.strict_exit_codes else 1

    dlq_uri: Optional[S3Uri] = None
    if not args.no_dlq:
        if args.dlq_prefix:
            try:
                dlq_uri = S3Uri.parse(args.dlq_prefix)
            except ValueError as e:
                _cli_log("error", f"invalid --dlq-prefix: {e}")
                return EXIT_CONFIG if args.strict_exit_codes else 1
        else:
            dlq_uri = job_uri.join("dlq")

    dest_auth = DestAuth(
        api_key=args.dest_api_key,
        api_key_encoded=args.dest_api_key_encoded,
        user=args.dest_user,
        password=args.dest_password,
    )

    try:
        s3 = make_s3_client(region=args.aws_region, endpoint_url=args.s3_endpoint_url)
    except Exception as e:  # noqa: BLE001 - surface to caller
        _cli_log("error", f"failed to create S3 client: {_redact_response_text(str(e))}")
        return EXIT_TRANSPORT if args.strict_exit_codes else 1

    try:
        parts, _has_manifest = _resolve_part_list(s3, job_uri)
    except _BadManifestError as e:
        _cli_log("error", f"manifest unreadable: {e}")
        return EXIT_CONFIG if args.strict_exit_codes else 1
    except Exception as e:  # noqa: BLE001 - listing/transport
        _cli_log("error", f"failed to enumerate S3 parts: {_redact_response_text(str(e))}")
        return EXIT_TRANSPORT if args.strict_exit_codes else 1

    if not parts:
        _cli_log("error", f"no NDJSON parts found under {job_uri}")
        return EXIT_CONFIG if args.strict_exit_codes else 1

    ckpt = load_checkpoint(args.checkpoint_file or "")
    completed: set = set(ckpt.get("completed_parts", []))
    if completed:
        _cli_log("info", f"resuming; {len(completed)} parts already completed")
    todo = [p for p in parts if p.key not in completed]

    batch_size_bytes = int(args.batch_size_mb * 1024 * 1024)

    totals = {
        "parts_completed_now": 0,
        "documents_succeeded": 0,
        "documents_failed": 0,
        "bytes_posted": 0,
        "failed_parts": 0,
    }
    transport_failure = False
    doc_failures = False

    with ThreadPoolExecutor(max_workers=args.max_in_flight) as pool:
        futures = {
            pool.submit(
                _load_part,
                s3,
                part,
                dest_host=args.dest_host or "",
                dest_auth=dest_auth,
                target_index_override=args.target_index,
                batch_size_bytes=batch_size_bytes,
                batch_max_items=args.batch_max_items,
                dlq_uri=dlq_uri,
                dry_run=args.dry_run,
            ): part
            for part in todo
        }
        for fut in as_completed(futures):
            part = futures[fut]
            try:
                result = fut.result()
            except Exception as e:  # noqa: BLE001
                _cli_log(
                    "error",
                    f"part {part.key} crashed: {_redact_response_text(str(e))}",
                )
                totals["failed_parts"] += 1
                transport_failure = True
                continue
            totals["documents_succeeded"] += int(result.get("success", 0))
            totals["documents_failed"] += int(result.get("errors", 0))
            totals["bytes_posted"] += int(result.get("bytes_posted", 0))
            if not result.get("ok"):
                totals["failed_parts"] += 1
                err = result.get("transport_error", "unknown")
                _cli_log("error", f"part {part.key}: {err}")
                if "DLQ disabled" in (err or ""):
                    doc_failures = True
                else:
                    transport_failure = True
                continue
            if int(result.get("errors", 0)) > 0:
                doc_failures = True
            totals["parts_completed_now"] += 1
            completed.add(part.key)
            _cli_log(
                "info",
                f"part {part.key} ok",
                success=int(result.get("success", 0)),
                errors=int(result.get("errors", 0)),
                dlq=result.get("dlq_uri"),
            )
            if args.checkpoint_file:
                save_checkpoint(
                    args.checkpoint_file,
                    {"completed_parts": sorted(completed)},
                )

    summary = {
        "ok": not transport_failure,
        "parts_total": len(parts),
        "parts_completed": len(completed),
        "documents_succeeded": totals["documents_succeeded"],
        "documents_failed": totals["documents_failed"],
        "bytes_posted": totals["bytes_posted"],
        "failed_parts": totals["failed_parts"],
        "dlq_used": doc_failures and dlq_uri is not None,
        "dry_run": args.dry_run,
    }
    if args.log_format == "json":
        print(json.dumps(summary))
    else:
        _cli_log("info", "load complete", **{k: v for k, v in summary.items() if k != "ok"})

    if transport_failure:
        return EXIT_TRANSPORT if args.strict_exit_codes else 1
    if doc_failures:
        if args.strict_exit_codes:
            return EXIT_DOC_ERRORS
        return 0 if not args.no_dlq else 1
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
