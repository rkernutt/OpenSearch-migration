#!/usr/bin/env python3
"""Migrate cluster-level metadata (templates and ingest pipelines) from a
source OpenSearch / Elasticsearch cluster to an Elastic destination.

Covers, in v1:

  * Legacy index templates  (``GET /_template``)
  * Composable index templates  (``GET /_index_template``)
  * Component templates  (``GET /_component_template``)
  * Ingest pipelines  (``GET /_ingest/pipeline``)

Index aliases are *not* covered here — they're applied per-index by the
chosen data-path tool (remote reindex, Logstash, ``s3_bulk_load``, RFS).
ILM policies and OpenSearch-specific objects (notifications, anomaly
detectors, ISM) are reported with a ``warn-and-skip`` for the operator to
translate by hand.

All bodies are passed through :mod:`metadata_migration.sanitizer` so the
target gets only allowed settings and a typeless mapping.

Reuses ``DestAuth``, ``_SESSION``, ``_redact_response_text``, and the
``_LOG_FORMAT_JSON`` toggle from ``validate_migration.py``. Mirrors
``s3_extract.py`` for source auth (basic + SigV4, optional ``--via-proxy``).

Exit codes (with ``--strict-exit-codes``):
  0  every selected object copied successfully
  2  configuration error (missing flags, no auth, unknown --include)
  3  transport / auth failure talking to source or destination
  4  one or more objects failed sanitization or PUT (others may have
     succeeded; rerun is safe — destination PUTs are idempotent)
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import bootstrap_env  # noqa: E402

bootstrap_env.load()

import validate_migration as _vm  # noqa: E402
from metadata_migration.sanitizer import (  # noqa: E402
    TARGET_ELASTICSEARCH_SERVERLESS,
    TARGET_TYPES,
    SanitizationReport,
    sanitize_index_body,
)
from validate_migration import (  # noqa: E402
    _SESSION,
    _TIMEOUT_SEARCH,
    _TIMEOUT_SHORT,
    DestAuth,
    _cli_log,
    _redact_response_text,
    opensearch_auth_sigv4,
)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_TRANSPORT = 3
EXIT_OBJECT_FAILURES = 4

_INCLUDE_DEFAULT = "templates,index_templates,component_templates,ingest_pipelines"
_VALID_INCLUDE = {
    "templates",  # legacy /_template
    "index_templates",  # composable /_index_template
    "component_templates",  # /_component_template
    "ingest_pipelines",  # /_ingest/pipeline
}


# ---------------------------------------------------------------------------
# Source auth (mirrors s3_extract.py / preflight.py)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SourceAuth:
    use_sigv4: bool
    region: str = "us-east-1"
    user: Optional[str] = None
    password: Optional[str] = None

    def request_auth(self) -> Any:
        if self.use_sigv4:
            return opensearch_auth_sigv4(self.region)
        if self.user is None or self.password is None:
            raise RuntimeError("basic auth requires --source-user and --source-password")
        return (self.user, self.password)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _get_source(host: str, path: str, auth: SourceAuth) -> Dict[str, Any]:
    url = host.rstrip("/") + path
    resp = _SESSION.get(url, auth=auth.request_auth(), timeout=_TIMEOUT_SEARCH)
    resp.raise_for_status()
    return resp.json()


def _put_dest(
    host: str,
    path: str,
    body: Dict[str, Any],
    dest_auth: DestAuth,
) -> requests.Response:
    headers, basic = dest_auth.apply({"Content-Type": "application/json"})
    url = host.rstrip("/") + path
    return _SESSION.put(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        auth=basic,
        timeout=_TIMEOUT_SEARCH,
    )


def _head_dest(host: str, path: str, dest_auth: DestAuth) -> int:
    headers, basic = dest_auth.apply()
    url = host.rstrip("/") + path
    return _SESSION.head(
        url, headers=headers or None, auth=basic, timeout=_TIMEOUT_SHORT
    ).status_code


# ---------------------------------------------------------------------------
# Per-object sanitization + write logic
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ObjectResult:
    kind: str
    name: str
    status: str  # "ok", "skipped-existing", "dry-run", "skipped-filter", "failed"
    detail: Optional[str] = None
    report: Optional[Dict[str, Any]] = None


def _name_filter(
    name: str,
    include_patterns: List[str],
    exclude_patterns: List[str],
) -> bool:
    """Return True if *name* should be processed."""
    if include_patterns and not any(fnmatch.fnmatch(name, p) for p in include_patterns):
        return False
    if any(fnmatch.fnmatch(name, p) for p in exclude_patterns):
        return False
    return True


def _is_system_template(name: str) -> bool:
    return name.startswith(".") or name.startswith("_")


def _process_template(
    name: str,
    body: Dict[str, Any],
    *,
    target_type: str,
    source_version: str,
) -> Tuple[Dict[str, Any], SanitizationReport]:
    """Apply sanitization to a legacy-template body."""
    return sanitize_index_body(body, source_version, target_type)


def _process_index_template(
    name: str,
    body: Dict[str, Any],
    *,
    target_type: str,
    source_version: str,
) -> Tuple[Dict[str, Any], SanitizationReport]:
    """Composable index templates: ``{"index_patterns": [...], "template": {...}, ...}``.

    Sanitize the inner ``template`` block (settings/mappings/aliases) only.
    """
    out = dict(body)
    report = SanitizationReport()
    template = out.get("template")
    if isinstance(template, dict):
        new_template, r = sanitize_index_body(template, source_version, target_type)
        out["template"] = new_template
        report = report.merge(r)
    return out, report


def _process_component_template(
    name: str,
    body: Dict[str, Any],
    *,
    target_type: str,
    source_version: str,
) -> Tuple[Dict[str, Any], SanitizationReport]:
    """Component templates wrap a ``template`` block with no ``index_patterns``."""
    return _process_index_template(
        name, body, target_type=target_type, source_version=source_version
    )


def _process_pipeline(
    name: str,
    body: Dict[str, Any],
    *,
    target_type: str,
    source_version: str,
) -> Tuple[Dict[str, Any], SanitizationReport]:
    """Ingest pipelines: passthrough for v1 (no Elastic-vs-OpenSearch processor delta we sanitize today)."""
    report = SanitizationReport()
    # Drop OpenSearch-only meta tags if present (rare on pipelines but keeps output clean).
    out = dict(body)
    for opensearch_only in ("opensearch_dashboards_meta", "opendistro_meta"):
        if opensearch_only in out:
            out.pop(opensearch_only)
            report.notes.append(f"removed {opensearch_only} from pipeline {name}")
    return out, report


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Migrate cluster-level metadata (templates, component templates, "
            "ingest pipelines) from OpenSearch to Elastic, with sanitization."
        ),
    )

    # source
    p.add_argument("--source-host", default=os.environ.get("SOURCE_OPENSEARCH_HOST"))
    p.add_argument("--source-user", default=os.environ.get("SOURCE_OPENSEARCH_USER"))
    p.add_argument("--source-password", default=os.environ.get("SOURCE_OPENSEARCH_PASSWORD"))
    p.add_argument("--source-region", default=os.environ.get("AWS_REGION", "us-east-1"))
    p.add_argument("--via-proxy", action="store_true", help="Cosmetic flag for audit logs.")

    # destination
    p.add_argument("--dest-host", default=os.environ.get("DEST_ELASTIC_HOST"))
    p.add_argument("--dest-api-key", default=os.environ.get("DEST_ELASTIC_API_KEY"))
    p.add_argument(
        "--dest-api-key-encoded",
        action="store_true",
        help="Set if --dest-api-key is already Base64-encoded.",
    )
    p.add_argument("--dest-user", default=os.environ.get("DEST_ELASTIC_USER"))
    p.add_argument("--dest-password", default=os.environ.get("DEST_ELASTIC_PASSWORD"))

    # what to migrate
    p.add_argument(
        "--include",
        default=_INCLUDE_DEFAULT,
        help=(
            f"Comma-separated kinds to migrate. Choices: "
            f"{', '.join(sorted(_VALID_INCLUDE))}. Default: {_INCLUDE_DEFAULT}."
        ),
    )
    p.add_argument(
        "--name",
        action="append",
        default=[],
        help=(
            "Glob to include (repeatable). Default: every non-system object. "
            "Example: --name 'logs-*' --name 'metrics-*'."
        ),
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Glob to exclude (repeatable). Combined with the default system filter.",
    )
    p.add_argument(
        "--keep-system-objects",
        action="store_true",
        help=(
            "Include objects whose name starts with '.' or '_' (default skips them, "
            "since they're typically managed by Elastic itself)."
        ),
    )
    p.add_argument(
        "--target-type",
        choices=TARGET_TYPES,
        default=TARGET_ELASTICSEARCH_SERVERLESS,
    )
    p.add_argument(
        "--source-version",
        default="autodetect",
        help="Hint source version (e.g. Elasticsearch_5_6). Affects mapping flatten.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="PUT even if the destination already has an object of that name.",
    )
    p.add_argument(
        "--report-dir",
        default=None,
        help="Optional directory to write sanitization reports per object.",
    )

    p.add_argument("--dry-run", action="store_true", help="Sanitize and report; do not PUT.")
    p.add_argument("--log-format", choices=("text", "json"), default="text")
    p.add_argument("--strict-exit-codes", action="store_true")
    return p.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> Optional[str]:
    if not args.source_host:
        return "--source-host (or SOURCE_OPENSEARCH_HOST) is required"
    if not args.dest_host and not args.dry_run:
        return "--dest-host (or DEST_ELASTIC_HOST) is required"
    if not args.dry_run and not (args.dest_api_key or (args.dest_user and args.dest_password)):
        return "set --dest-api-key (or DEST_ELASTIC_*) or --dest-user / --dest-password"
    bad = [k for k in args.include.split(",") if k.strip() and k.strip() not in _VALID_INCLUDE]
    if bad:
        return f"unknown --include kinds: {bad} (valid: {sorted(_VALID_INCLUDE)})"
    return None


# ---------------------------------------------------------------------------
# Per-kind drivers
# ---------------------------------------------------------------------------


_KIND_CONFIG: Dict[str, Dict[str, Any]] = {
    "templates": {
        "list_path": "/_template",
        "destination_path": lambda name: f"/_template/{name}",
        "process": _process_template,
        "label": "legacy template",
        "extract_map": lambda payload: payload,  # already {name: body}
    },
    "index_templates": {
        "list_path": "/_index_template",
        "destination_path": lambda name: f"/_index_template/{name}",
        "process": _process_index_template,
        "label": "index template",
        "extract_map": lambda payload: {
            entry["name"]: entry["index_template"] for entry in payload.get("index_templates", [])
        },
    },
    "component_templates": {
        "list_path": "/_component_template",
        "destination_path": lambda name: f"/_component_template/{name}",
        "process": _process_component_template,
        "label": "component template",
        "extract_map": lambda payload: {
            entry["name"]: entry["component_template"]
            for entry in payload.get("component_templates", [])
        },
    },
    "ingest_pipelines": {
        "list_path": "/_ingest/pipeline",
        "destination_path": lambda name: f"/_ingest/pipeline/{name}",
        "process": _process_pipeline,
        "label": "ingest pipeline",
        "extract_map": lambda payload: payload,
    },
}


def _process_kind(
    kind: str,
    args: argparse.Namespace,
    source_auth: SourceAuth,
    dest_auth: DestAuth,
    report_dir: Optional[Path],
) -> List[ObjectResult]:
    cfg = _KIND_CONFIG[kind]
    process: Callable[..., Tuple[Dict[str, Any], SanitizationReport]] = cfg["process"]
    list_path: str = cfg["list_path"]
    extract: Callable[[Dict[str, Any]], Dict[str, Any]] = cfg["extract_map"]
    dest_path_for: Callable[[str], str] = cfg["destination_path"]

    payload = _get_source(args.source_host, list_path, source_auth)
    if not isinstance(payload, dict):
        return [ObjectResult(kind=kind, name="", status="failed", detail="non-dict response")]
    objects = extract(payload)
    if not isinstance(objects, dict):
        return [
            ObjectResult(kind=kind, name="", status="failed", detail="unexpected response shape")
        ]

    results: List[ObjectResult] = []
    for name, body in sorted(objects.items()):
        if not args.keep_system_objects and _is_system_template(name):
            results.append(ObjectResult(kind, name, "skipped-filter", "system object"))
            continue
        if not _name_filter(name, args.name, args.exclude):
            results.append(ObjectResult(kind, name, "skipped-filter", "filter"))
            continue
        try:
            new_body, sanitization = process(
                name, body, target_type=args.target_type, source_version=args.source_version
            )
        except Exception as e:  # noqa: BLE001
            results.append(ObjectResult(kind, name, "failed", f"sanitize error: {e}"))
            continue

        if report_dir is not None:
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / f"{kind}__{name}.json").write_text(
                json.dumps(
                    {"sanitized": new_body, "report": sanitization.to_dict()},
                    indent=2,
                ),
                encoding="utf-8",
            )

        if args.dry_run:
            results.append(
                ObjectResult(
                    kind,
                    name,
                    "dry-run",
                    report=sanitization.to_dict() if not sanitization.is_empty() else None,
                )
            )
            continue

        path = dest_path_for(name)
        if not args.overwrite:
            try:
                existing = _head_dest(args.dest_host, path, dest_auth)
            except requests.RequestException as e:
                results.append(
                    ObjectResult(
                        kind, name, "failed", f"HEAD {path} failed: {_redact_response_text(str(e))}"
                    )
                )
                continue
            if existing == 200:
                results.append(ObjectResult(kind, name, "skipped-existing", f"already at {path}"))
                continue

        try:
            resp = _put_dest(args.dest_host, path, new_body, dest_auth)
        except requests.RequestException as e:
            results.append(
                ObjectResult(kind, name, "failed", f"PUT {path}: {_redact_response_text(str(e))}")
            )
            continue
        if resp.status_code >= 400:
            results.append(
                ObjectResult(
                    kind,
                    name,
                    "failed",
                    f"PUT {path} HTTP {resp.status_code}: {_redact_response_text(resp.text[:300])}",
                )
            )
            continue
        results.append(
            ObjectResult(
                kind,
                name,
                "ok",
                report=sanitization.to_dict() if not sanitization.is_empty() else None,
            )
        )
    return results


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

    use_sigv4 = not (args.source_user and args.source_password)
    source_auth = SourceAuth(
        use_sigv4=use_sigv4,
        region=args.source_region,
        user=args.source_user,
        password=args.source_password,
    )
    dest_auth = DestAuth(
        api_key=args.dest_api_key,
        api_key_encoded=args.dest_api_key_encoded,
        user=args.dest_user,
        password=args.dest_password,
    )

    report_dir = Path(args.report_dir) if args.report_dir else None
    kinds = [k.strip() for k in args.include.split(",") if k.strip()]

    started = time.monotonic()
    all_results: List[ObjectResult] = []
    transport_failure = False
    for kind in kinds:
        try:
            results = _process_kind(kind, args, source_auth, dest_auth, report_dir)
        except requests.RequestException as e:
            _cli_log(
                "error",
                f"failed to read {kind} from source: {_redact_response_text(str(e))}",
            )
            transport_failure = True
            continue
        all_results.extend(results)

    summary: Dict[str, Any] = {
        "ok": not transport_failure and not any(r.status == "failed" for r in all_results),
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "by_status": {},
        "failed": [],
        "dry_run": args.dry_run,
    }
    for r in all_results:
        summary["by_status"][r.status] = summary["by_status"].get(r.status, 0) + 1
        if r.status == "failed":
            summary["failed"].append({"kind": r.kind, "name": r.name, "detail": r.detail})
        _cli_log(
            "info"
            if r.status in ("ok", "dry-run", "skipped-existing", "skipped-filter")
            else "error",
            f"{r.kind} {r.name} {r.status}",
            detail=r.detail,
        )

    if args.log_format == "json":
        print(json.dumps(summary))

    if transport_failure:
        return EXIT_TRANSPORT if args.strict_exit_codes else 1
    if any(r.status == "failed" for r in all_results):
        return EXIT_OBJECT_FAILURES if args.strict_exit_codes else 1
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
