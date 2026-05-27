#!/usr/bin/env python3
"""Compatibility pre-flight: classify a source OpenSearch (or ES) cluster
against an Elasticsearch destination and recommend a migration path.

Run this BEFORE picking a data path (Logstash, S3 staging, RFS, remote
reindex). It surfaces the per-cluster and per-index quirks that affect
which paths in this repo will work cleanly:

  * cluster version + Lucene version compatibility window;
  * k-NN indices (``index.knn``) — RFS cannot reconstruct OS-specific
    vector segment formats; document-streaming paths still work but
    require re-embedding on the destination;
  * custom Lucene codecs (``index.codec`` other than default /
    best_compression) — RFS may not have the codec available;
  * Serverless-forbidden settings on a destination of
    ``ELASTICSEARCH_SERVERLESS`` — flagged for sanitization;
  * ES 5/6-era mapping artefacts (``string`` type, multi-type, ``_all``,
    ``_timestamp``, etc.) — flagged for ``metadata_migration`` sanitizer.

Reads source/destination credentials from the same env vars as the rest
of the toolkit (``SOURCE_OPENSEARCH_*``, ``DEST_ELASTIC_*``,
``AWS_REGION``).

Exit codes (with ``--strict-exit-codes``):

  0   no compatibility issues found; any data path will work.
  2   misconfiguration (missing host/auth, unreadable filter, etc.).
  3   transport / auth / TLS failure on source or destination.
  4   compatibility issues found; document-streaming paths (B / D) still
      work but RFS (Path E) and/or native snapshot restore have caveats.
      Per-index report lists exactly which.

Without ``--strict-exit-codes``, exit codes collapse to 0 / 1.
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import json
import os
from typing import Any, Iterable

import requests

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_TRANSPORT = 3
EXIT_INCOMPAT = 4

# Lucene -> ES compatibility window.
#   ES reads Lucene N-1 and N for indices.
#   OS 1.x = Lucene 8, OS 2.x = Lucene 9, OS 3.x = Lucene 10.
#   ES 7.x = Lucene 8, ES 8.x = Lucene 9, ES 9.x = Lucene 10.
_LUCENE_TO_MIN_ES_MAJOR: dict[int, int] = {8: 7, 9: 8, 10: 9}

# Subset of OS-only / problematic codecs. Anything outside the ES default
# set will at minimum need RFS to ship the matching codec; some are
# OpenSearch-specific (zstd*, qat*).
_OPENSEARCH_ONLY_CODECS: set[str] = {
    "zstd",
    "zstd_no_dict",
    "qat_lz4",
    "qat_deflate",
}
_ES_KNOWN_CODECS: set[str] = {"default", "best_compression", "lucene_default"}

# index.* settings forbidden on Elastic Cloud Serverless. Mirrors the list
# in metadata_migration.sanitizer; kept locally so this module is
# importable without depending on metadata_migration.
_SERVERLESS_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "index.number_of_shards",
    "index.number_of_replicas",
    "index.refresh_interval",
    "index.translog.",
    "index.merge.",
    "index.store.",
    "index.routing.",
    "index.shard.",
    "index.allocation.",
    "index.codec",
    "index.soft_deletes.",
    "index.search.idle.",
    "index.write.",
    "index.search.slowlog.",
    "index.indexing.slowlog.",
)

# Deprecated mapping options removed in ES 7+.
_DEPRECATED_MAPPING_KEYS: set[str] = {
    "_all",
    "_timestamp",
    "_ttl",
    "_size",
    "_parent",
    "include_in_all",
}

# System-ish indices we skip by default.
_SYSTEM_PATTERNS: tuple[str, ...] = (
    ".*",
    "ilm-history*",
    "logstash-*",  # users override if they really mean to migrate these
    "migrations_working_state",  # upstream RFS coordination index
    "apm-*-internal*",
    "kibana_sample_data_*",
)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ClusterInfo:
    host: str
    distribution: str  # "opensearch" | "elasticsearch" | "unknown"
    version: str
    major: int
    lucene_version: str | None
    lucene_major: int | None
    build_flavor: str | None  # "default" | "serverless" | None


@dataclasses.dataclass
class IndexFinding:
    index: str
    severity: str  # "ok" | "warn" | "block-rfs" | "block-snapshot-restore"
    issues: list[str]
    settings_flags: list[str]
    mapping_flags: list[str]


@dataclasses.dataclass
class Report:
    source: ClusterInfo
    dest: ClusterInfo | None
    cluster_warnings: list[str]
    indices: list[IndexFinding]

    @property
    def has_warnings(self) -> bool:
        return bool(self.cluster_warnings) or any(f.severity != "ok" for f in self.indices)

    def to_json(self) -> dict[str, Any]:
        return {
            "source": dataclasses.asdict(self.source),
            "dest": dataclasses.asdict(self.dest) if self.dest else None,
            "cluster_warnings": self.cluster_warnings,
            "indices": [dataclasses.asdict(f) for f in self.indices],
            "summary": {
                "total_indices": len(self.indices),
                "ok": sum(1 for f in self.indices if f.severity == "ok"),
                "warn": sum(1 for f in self.indices if f.severity == "warn"),
                "block_rfs": sum(1 for f in self.indices if f.severity == "block-rfs"),
                "block_snapshot_restore": sum(
                    1 for f in self.indices if f.severity == "block-snapshot-restore"
                ),
            },
        }


# ---------------------------------------------------------------------------
# Cluster + per-index probing
# ---------------------------------------------------------------------------


def _parse_major(version: str) -> int:
    try:
        return int(version.split(".")[0])
    except (ValueError, IndexError):
        return 0


def probe_cluster(
    host: str,
    *,
    headers: dict[str, str],
    auth: Any,
    label: str,
    session: requests.Session,
    timeout: float,
) -> ClusterInfo:
    r = session.get(host.rstrip("/") + "/", headers=headers, auth=auth, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    v = body.get("version", {})
    distribution = v.get("distribution") or ("elasticsearch" if v.get("number") else "unknown")
    version = v.get("number", "0.0.0")
    lucene = v.get("lucene_version")
    return ClusterInfo(
        host=host,
        distribution=distribution,
        version=version,
        major=_parse_major(version),
        lucene_version=lucene,
        lucene_major=_parse_major(lucene) if lucene else None,
        build_flavor=v.get("build_flavor"),
    )


def list_source_indices(
    host: str,
    *,
    headers: dict[str, str],
    auth: Any,
    session: requests.Session,
    timeout: float,
    include: list[str],
    exclude: list[str],
    keep_system: bool,
) -> list[str]:
    r = session.get(
        host.rstrip("/") + "/_cat/indices",
        params={"format": "json", "h": "index"},
        headers=headers,
        auth=auth,
        timeout=timeout,
    )
    r.raise_for_status()
    names = [row["index"] for row in r.json()]

    def _matches(name: str, patterns: Iterable[str]) -> bool:
        return any(fnmatch.fnmatchcase(name, p) for p in patterns)

    if include:
        names = [n for n in names if _matches(n, include)]
    if exclude:
        names = [n for n in names if not _matches(n, exclude)]
    if not keep_system:
        names = [n for n in names if not _matches(n, _SYSTEM_PATTERNS)]

    return sorted(names)


def _flatten_settings(settings: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in settings.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten_settings(v, prefix=key + "."))
        else:
            out[key] = v
    return out


def inspect_index(
    host: str,
    index: str,
    *,
    headers: dict[str, str],
    auth: Any,
    session: requests.Session,
    timeout: float,
    target_type: str | None,
) -> IndexFinding:
    issues: list[str] = []
    settings_flags: list[str] = []
    mapping_flags: list[str] = []
    severity = "ok"

    # -- settings -----------------------------------------------------------
    s = session.get(
        host.rstrip("/") + f"/{index}/_settings",
        headers=headers,
        auth=auth,
        timeout=timeout,
    )
    s.raise_for_status()
    raw_settings = next(iter(s.json().values())).get("settings", {})
    flat = _flatten_settings(raw_settings)

    knn = flat.get("index.knn")
    if str(knn).lower() == "true":
        settings_flags.append("index.knn=true")
        issues.append(
            "k-NN index. RFS cannot reconstruct OS k-NN vector segments. "
            "Use Path D (S3 staging) and re-embed on the destination."
        )
        severity = "block-rfs"

    codec = flat.get("index.codec")
    if codec and codec not in _ES_KNOWN_CODECS:
        settings_flags.append(f"index.codec={codec}")
        if codec in _OPENSEARCH_ONLY_CODECS:
            issues.append(
                f"OpenSearch-only codec '{codec}'. RFS may fail to read "
                "segments. Use Path D or Path B."
            )
            severity = _max_severity(severity, "block-rfs")
        else:
            issues.append(
                f"Non-default codec '{codec}'. Confirm RFS bundles it; Path D / B are unaffected."
            )
            severity = _max_severity(severity, "warn")

    if target_type == "ELASTICSEARCH_SERVERLESS":
        forbidden = [
            k for k in flat if any(k.startswith(p) for p in _SERVERLESS_FORBIDDEN_PREFIXES)
        ]
        if forbidden:
            settings_flags.extend(forbidden[:8])
            issues.append(
                f"{len(forbidden)} setting(s) forbidden on Serverless. "
                "metadata_migration sanitizer strips these automatically."
            )
            severity = _max_severity(severity, "warn")

    # -- mappings ----------------------------------------------------------
    m = session.get(
        host.rstrip("/") + f"/{index}/_mapping",
        headers=headers,
        auth=auth,
        timeout=timeout,
    )
    m.raise_for_status()
    raw_mappings = next(iter(m.json().values())).get("mappings", {})
    mapping_flags.extend(_inspect_mapping(raw_mappings))
    if mapping_flags:
        issues.append(
            f"{len(mapping_flags)} mapping issue(s); metadata_migration "
            "sanitizer translates ES 5/6 artefacts to typeless mappings."
        )
        severity = _max_severity(severity, "warn")

    return IndexFinding(
        index=index,
        severity=severity,
        issues=issues,
        settings_flags=settings_flags,
        mapping_flags=mapping_flags,
    )


def _inspect_mapping(mappings: dict[str, Any]) -> list[str]:
    flags: list[str] = []

    # Multi-type mapping (ES 5/6): top-level keys are type names rather than
    # the typeless ``properties`` / ``dynamic`` / ``_meta`` etc.
    typeless_keys = {
        "properties",
        "dynamic",
        "dynamic_templates",
        "_meta",
        "_routing",
        "_source",
        "_size",
        "runtime",
        "date_detection",
        "numeric_detection",
        "dynamic_date_formats",
        "enabled",
    }
    type_names = [k for k in mappings if k not in typeless_keys]
    if type_names and any(k in mappings for k in ("properties", "dynamic")):
        flags.append(f"multi-type-mapping ({len(type_names)} type(s))")
    elif type_names and len(type_names) > 1:
        flags.append(f"multi-type-mapping ({len(type_names)} type(s))")
    elif type_names and type_names[0] != "_doc":
        flags.append(f"legacy-type-name={type_names[0]}")

    # Deprecated top-level options.
    for k in mappings:
        if k in _DEPRECATED_MAPPING_KEYS:
            flags.append(f"deprecated-option={k}")

    # `string` type fields (ES 5/6) anywhere in the properties tree.
    if _has_string_type(mappings):
        flags.append("legacy-string-type")

    return flags


def _has_string_type(node: Any) -> bool:
    if isinstance(node, dict):
        if node.get("type") == "string":
            return True
        return any(_has_string_type(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_string_type(v) for v in node)
    return False


_SEVERITY_RANK = {
    "ok": 0,
    "warn": 1,
    "block-snapshot-restore": 2,
    "block-rfs": 3,
}


def _max_severity(a: str, b: str) -> str:
    return a if _SEVERITY_RANK[a] >= _SEVERITY_RANK[b] else b


# ---------------------------------------------------------------------------
# Cluster-level cross-checks
# ---------------------------------------------------------------------------


def cluster_warnings(source: ClusterInfo, dest: ClusterInfo | None) -> list[str]:
    out: list[str] = []
    if source.lucene_major is not None:
        min_es = _LUCENE_TO_MIN_ES_MAJOR.get(source.lucene_major)
        if min_es is None:
            out.append(
                f"Source Lucene {source.lucene_version} is outside the "
                "tested OS->ES window (Lucene 8/9/10). Path E (RFS) may "
                "not read segments; document-streaming paths still work."
            )
        elif dest is not None and dest.major and dest.major < min_es:
            out.append(
                f"Source Lucene {source.lucene_version} requires ES "
                f"{min_es}.x or newer for native snapshot restore / RFS; "
                f"destination is ES {dest.version}. Use Path B / D."
            )
    if source.distribution == "opensearch" and source.major >= 3:
        out.append(
            "Source is OpenSearch 3.x (Lucene 10). Pilot RFS on a small "
            "index first to confirm; document-streaming paths are safe."
        )
    if dest and dest.build_flavor == "serverless":
        out.append(
            "Destination is Elastic Cloud Serverless. Run "
            "metadata_migration with --target-type ELASTICSEARCH_SERVERLESS "
            "before any data path; native snapshot restore not supported."
        )
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Pre-flight compatibility check between an OpenSearch (or ES) "
            "source and an Elasticsearch destination. Recommends which "
            "migration path(s) in this repo are safe to use."
        )
    )
    p.add_argument("--source-host", default=os.environ.get("SOURCE_OPENSEARCH_HOST"))
    p.add_argument("--source-user", default=os.environ.get("SOURCE_OPENSEARCH_USER"))
    p.add_argument("--source-password", default=os.environ.get("SOURCE_OPENSEARCH_PASSWORD"))
    p.add_argument("--source-region", default=os.environ.get("AWS_REGION", "us-east-1"))

    p.add_argument(
        "--dest-host",
        default=os.environ.get("DEST_ELASTIC_HOST"),
        help="Optional. If set, also probes the destination and adds Lucene/Serverless cross-checks.",
    )
    p.add_argument("--dest-api-key", default=os.environ.get("DEST_ELASTIC_API_KEY"))
    p.add_argument(
        "--dest-api-key-encoded",
        action="store_true",
        help="Treat --dest-api-key as the already-encoded base64 form.",
    )
    p.add_argument("--dest-user", default=os.environ.get("DEST_ELASTIC_USER"))
    p.add_argument("--dest-password", default=os.environ.get("DEST_ELASTIC_PASSWORD"))

    p.add_argument(
        "--target-type",
        choices=("ELASTICSEARCH", "ELASTICSEARCH_SERVERLESS"),
        default=None,
        help="Override destination type for Serverless-forbidden settings checks. "
        "If unset, inferred from --dest-host build_flavor when available.",
    )
    p.add_argument(
        "--include",
        action="append",
        default=[],
        help="fnmatch glob to include source indices (repeatable).",
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="fnmatch glob to exclude source indices (repeatable).",
    )
    p.add_argument(
        "--keep-system",
        action="store_true",
        help="Include `.`-prefixed and other system indices in the scan.",
    )
    p.add_argument(
        "--max-indices",
        type=int,
        default=200,
        help="Cap on indices inspected (default 200; raise for very large clusters).",
    )
    p.add_argument(
        "--report",
        default=None,
        help="Optional path to write the full JSON report.",
    )
    p.add_argument("--log-format", choices=("text", "json"), default="text")
    p.add_argument("--strict-exit-codes", action="store_true")
    p.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="HTTP timeout per request (default 30s).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    import bootstrap_env

    bootstrap_env.load()

    from validate_migration import (
        _SESSION,
        DestAuth,
        _cli_log,
        _redact_response_text,
        opensearch_auth_sigv4,
    )

    args = _build_parser().parse_args(argv)

    if args.log_format == "json":
        os.environ["VALIDATION_LOG_FORMAT"] = "json"

    if not args.source_host:
        _cli_log("error", "SOURCE_OPENSEARCH_HOST is required (env or --source-host).")
        return EXIT_CONFIG if args.strict_exit_codes else 1

    # Source auth: SigV4 unless basic creds provided.
    src_headers: dict[str, str] = {}
    src_auth: Any
    if args.source_user and args.source_password:
        src_auth = (args.source_user, args.source_password)
    else:
        try:
            src_auth = opensearch_auth_sigv4(args.source_region)
        except Exception as e:
            _cli_log(
                "error",
                f"Could not build SigV4 auth for source: {e}. "
                "Set --source-user/--source-password for basic auth.",
            )
            return EXIT_CONFIG if args.strict_exit_codes else 1

    dest_info: ClusterInfo | None = None
    target_type = args.target_type
    dest_headers: dict[str, str] = {}
    dest_basic: Any = None

    if args.dest_host:
        if not (args.dest_api_key or (args.dest_user and args.dest_password)):
            _cli_log(
                "error",
                "Provide --dest-api-key or (--dest-user and --dest-password) for the destination.",
            )
            return EXIT_CONFIG if args.strict_exit_codes else 1
        dest_auth_obj = DestAuth(
            api_key=args.dest_api_key,
            api_key_encoded=args.dest_api_key_encoded,
            user=args.dest_user,
            password=args.dest_password,
        )
        dest_headers, dest_basic = dest_auth_obj.apply()

    try:
        source_info = probe_cluster(
            args.source_host,
            headers=src_headers,
            auth=src_auth,
            label="source",
            session=_SESSION,
            timeout=args.timeout_seconds,
        )
        if args.dest_host:
            dest_info = probe_cluster(
                args.dest_host,
                headers=dest_headers,
                auth=dest_basic,
                label="dest",
                session=_SESSION,
                timeout=args.timeout_seconds,
            )
            if target_type is None and dest_info.build_flavor == "serverless":
                target_type = "ELASTICSEARCH_SERVERLESS"
    except requests.RequestException as e:
        msg = f"Cluster probe failed: {e}"
        if e.response is not None:
            msg += f" — {_redact_response_text(e.response.text[:500])}"
        _cli_log("error", msg)
        return EXIT_TRANSPORT if args.strict_exit_codes else 1

    # Discover and inspect indices.
    try:
        index_names = list_source_indices(
            args.source_host,
            headers=src_headers,
            auth=src_auth,
            session=_SESSION,
            timeout=args.timeout_seconds,
            include=args.include,
            exclude=args.exclude,
            keep_system=args.keep_system,
        )
    except requests.RequestException as e:
        _cli_log("error", f"Listing source indices failed: {e}")
        return EXIT_TRANSPORT if args.strict_exit_codes else 1

    if len(index_names) > args.max_indices:
        _cli_log(
            "warn",
            f"Found {len(index_names)} indices; inspecting first {args.max_indices}. "
            "Re-run with --max-indices to expand.",
        )
        index_names = index_names[: args.max_indices]

    findings: list[IndexFinding] = []
    for name in index_names:
        try:
            findings.append(
                inspect_index(
                    args.source_host,
                    name,
                    headers=src_headers,
                    auth=src_auth,
                    session=_SESSION,
                    timeout=args.timeout_seconds,
                    target_type=target_type,
                )
            )
        except requests.RequestException as e:
            _cli_log("warn", f"Skipping {name}: {e}")
            findings.append(
                IndexFinding(
                    index=name,
                    severity="warn",
                    issues=[f"probe failed: {e}"],
                    settings_flags=[],
                    mapping_flags=[],
                )
            )

    report = Report(
        source=source_info,
        dest=dest_info,
        cluster_warnings=cluster_warnings(source_info, dest_info),
        indices=findings,
    )

    _emit_summary(report, args.log_format)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report.to_json(), fh, indent=2)

    return EXIT_INCOMPAT if (args.strict_exit_codes and report.has_warnings) else EXIT_OK


def _emit_summary(report: Report, log_format: str) -> None:
    summary = report.to_json()
    if log_format == "json":
        print(json.dumps(summary, indent=2))
        return

    src = report.source
    print(f"Source: {src.distribution} {src.version} (Lucene {src.lucene_version})")
    if report.dest:
        dst = report.dest
        flavor = f" [{dst.build_flavor}]" if dst.build_flavor else ""
        print(f"Dest:   {dst.distribution} {dst.version}{flavor} (Lucene {dst.lucene_version})")
    else:
        print("Dest:   not probed (--dest-host not set)")

    if report.cluster_warnings:
        print("\nCluster warnings:")
        for w in report.cluster_warnings:
            print(f"  - {w}")

    sev_counts = summary["summary"]
    print(
        f"\nIndices: {sev_counts['total_indices']} scanned "
        f"(ok={sev_counts['ok']}, warn={sev_counts['warn']}, "
        f"block-rfs={sev_counts['block_rfs']})"
    )

    flagged = [f for f in report.indices if f.severity != "ok"]
    if flagged:
        print("\nIndex findings:")
        for f in flagged:
            print(f"  [{f.severity}] {f.index}")
            for issue in f.issues:
                print(f"      - {issue}")
            for flag in f.settings_flags:
                print(f"      settings: {flag}")
            for flag in f.mapping_flags:
                print(f"      mapping:  {flag}")

    print("\nRecommendation:")
    if any(f.severity == "block-rfs" for f in report.indices):
        print(
            "  Use Path D (S3 staging) or Path B (Logstash). At least one "
            "index has features RFS cannot reconstruct."
        )
    elif report.has_warnings:
        print(
            "  Path D / B are safe. RFS (Path E) is likely fine but pilot "
            "the flagged indices first; metadata_migration sanitizer "
            "handles the mapping/settings warnings."
        )
    else:
        print("  All paths (B, D, E) are clean for the scanned indices.")


if __name__ == "__main__":
    raise SystemExit(main())
