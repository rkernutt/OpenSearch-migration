"""Pure-Python sanitizers for index settings and mappings.

Two operations:

  * ``sanitize_index_settings(settings, target_type)`` — strip settings that
    aren't permitted on the target. The Serverless list mirrors what the
    upstream OpenSearch Migrations RFS tool does for
    ``--target-type ELASTICSEARCH_SERVERLESS``.
  * ``sanitize_mapping(mapping, source_version, target_type)`` — apply
    multi-version mapping translations:

    - Multi-type indices (Elasticsearch 5.x): flatten into a single typeless
      mapping. The first non-default type is preserved as ``properties`` and
      conflicts across types are reported.
    - Drop deprecated fields (``_all``, ``_default_``).
    - ``string`` → ``text`` / ``keyword`` (with a multi-field for
      ``not_analyzed``-style strings).
    - Drop ``include_in_all``, ``boost`` and other legacy mapping options.

Both helpers are pure functions over JSON-shaped dicts; they never touch the
network. The CLI at the bottom is a small convenience wrapper for sanitizing
a JSON file from the shell.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Target / source types
# ---------------------------------------------------------------------------

TARGET_ELASTICSEARCH = "ELASTICSEARCH"
TARGET_ELASTICSEARCH_SERVERLESS = "ELASTICSEARCH_SERVERLESS"
TARGET_OPENSEARCH = "OPENSEARCH"
TARGET_TYPES = (TARGET_ELASTICSEARCH, TARGET_ELASTICSEARCH_SERVERLESS, TARGET_OPENSEARCH)

# Source-version tags (mirror the upstream RFS naming so users have one vocabulary).
# Anything 5.x or earlier triggers multi-type flattening; 6.x is single-type so it's
# already flat (only `_all` etc. need stripping); 7+/Serverless is typeless.
SOURCE_VERSION_AUTODETECT = "autodetect"


# ---------------------------------------------------------------------------
# Settings sanitization
# ---------------------------------------------------------------------------


# Settings that Serverless does not allow to be specified by the user. Values
# under any of these prefixes (with `.` as the separator) are stripped.
_SERVERLESS_FORBIDDEN_PREFIXES: Tuple[str, ...] = (
    "index.number_of_shards",
    "index.number_of_replicas",
    "index.auto_expand_replicas",
    "index.routing.allocation",
    "index.codec",
    "index.translog",
    "index.merge",
    "index.store",
    "index.unassigned",
    "index.allocation",
    "index.shard.check_on_startup",
    "index.search.idle.after",
)

# OpenSearch-only settings that are always meaningless on an Elasticsearch
# destination. Strip these regardless of target type.
_OPENSEARCH_ONLY_PREFIXES: Tuple[str, ...] = (
    "index.knn",
    "index.replication.type",
    "index.plugins.replication",
    "index.opendistro",
    "index.opendistro_security",
    "index.opensearch",
)


@dataclasses.dataclass
class SanitizationReport:
    """What the sanitizer kept, removed, or rewrote.

    Designed to be readable in JSON output and easy to assert against in
    tests.
    """

    removed_settings: List[str] = dataclasses.field(default_factory=list)
    removed_mapping_fields: List[str] = dataclasses.field(default_factory=list)
    rewrote_mapping_fields: List[str] = dataclasses.field(default_factory=list)
    type_conflicts: List[str] = dataclasses.field(default_factory=list)
    flattened_types: List[str] = dataclasses.field(default_factory=list)
    notes: List[str] = dataclasses.field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.removed_settings
            or self.removed_mapping_fields
            or self.rewrote_mapping_fields
            or self.type_conflicts
            or self.flattened_types
            or self.notes
        )

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def merge(self, other: "SanitizationReport") -> "SanitizationReport":
        return SanitizationReport(
            removed_settings=self.removed_settings + other.removed_settings,
            removed_mapping_fields=self.removed_mapping_fields + other.removed_mapping_fields,
            rewrote_mapping_fields=self.rewrote_mapping_fields + other.rewrote_mapping_fields,
            type_conflicts=self.type_conflicts + other.type_conflicts,
            flattened_types=self.flattened_types + other.flattened_types,
            notes=self.notes + other.notes,
        )


def _walk_settings_paths(
    settings: Dict[str, Any],
    prefix: str = "",
) -> List[Tuple[str, Any]]:
    """Yield ``(dotted_path, value)`` pairs for every leaf in *settings*."""
    out: List[Tuple[str, Any]] = []
    for key, value in settings.items():
        path = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            out.extend(_walk_settings_paths(value, path))
        else:
            out.append((path, value))
    return out


def _set_nested(target: Dict[str, Any], dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    node = target
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            # Path conflict; abandon (caller should not have produced this).
            return
    node[parts[-1]] = value


def _path_matches_prefix(path: str, prefixes: Tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p + ".") for p in prefixes)


def sanitize_index_settings(
    settings: Dict[str, Any],
    target_type: str = TARGET_ELASTICSEARCH_SERVERLESS,
) -> Tuple[Dict[str, Any], SanitizationReport]:
    """Return a copy of *settings* with disallowed keys stripped.

    Accepts both flat (``"index.number_of_shards": 3``) and nested
    (``{"index": {"number_of_shards": 3}}``) representations and emits a
    nested form on output, mirroring how Elasticsearch reports settings.

    The Serverless forbidden list mirrors what upstream RFS strips for
    ``--target-type ELASTICSEARCH_SERVERLESS``. OpenSearch-only settings
    (`knn`, replication, opendistro, …) are always removed regardless of
    target — they're meaningless on Elasticsearch.
    """
    if target_type not in TARGET_TYPES:
        raise ValueError(f"unknown target_type: {target_type!r}")

    forbidden_prefixes: List[str] = list(_OPENSEARCH_ONLY_PREFIXES)
    if target_type == TARGET_ELASTICSEARCH_SERVERLESS:
        forbidden_prefixes.extend(_SERVERLESS_FORBIDDEN_PREFIXES)

    out: Dict[str, Any] = {}
    report = SanitizationReport()
    for path, value in _walk_settings_paths(settings):
        if _path_matches_prefix(path, tuple(forbidden_prefixes)):
            report.removed_settings.append(path)
            continue
        _set_nested(out, path, value)
    return out, report


# ---------------------------------------------------------------------------
# Mapping sanitization
# ---------------------------------------------------------------------------


# Top-level mapping keys that look like type names (rather than mapping config).
_RESERVED_MAPPING_KEYS: Set[str] = {
    "properties",
    "_meta",
    "_source",
    "_routing",
    "_field_names",
    "_size",
    "_doc",  # ES 6.x typeless marker
    "dynamic",
    "dynamic_templates",
    "date_detection",
    "numeric_detection",
    "dynamic_date_formats",
    "runtime",
    "subobjects",
    "enabled",
}

# Per-field options that are deprecated / removed in ES 7+.
_DEPRECATED_FIELD_OPTIONS: Set[str] = {
    "include_in_all",
    "boost",
    "index_options_offsets",
    "norms.enabled",
    "fielddata_frequency_filter",
    "position_increment_gap",  # kept on text fields, removed elsewhere
}

# Whole-mapping options removed in 7+/Serverless. Note that ``_default_`` is
# *not* in this set: in ES 5.x it's a type-shaped block (with ``properties``),
# and ``_flatten_multi_type`` handles dropping it with a clearer note.
_DEPRECATED_TOP_LEVEL_OPTIONS: Set[str] = {"_all", "_timestamp", "_ttl"}


def _has_multiple_types(mapping: Dict[str, Any]) -> List[str]:
    """Return any non-reserved top-level keys that look like type names."""
    if not isinstance(mapping, dict):
        return []
    return [
        k
        for k in mapping.keys()
        if isinstance(mapping.get(k), dict)
        and k not in _RESERVED_MAPPING_KEYS
        and "properties" in (mapping.get(k) or {})
    ]


def _flatten_multi_type(
    mapping: Dict[str, Any],
    report: SanitizationReport,
) -> Dict[str, Any]:
    """Flatten an ES 5.x multi-type mapping into a single typeless mapping.

    Strategy:
      * Drop ``_default_`` entirely (it's a template, not a type).
      * Pick the *first* concrete type as the base.
      * Merge any other types' ``properties`` on top, recording conflicts.
      * Merge other top-level config (``_meta``, ``_source``, ``dynamic``,
        ``dynamic_templates``) — first writer wins.
    """
    type_names = _has_multiple_types(mapping)
    if "_default_" in mapping:
        report.notes.append("dropped _default_ template type")
        # remove _default_ from candidate list and from input
        mapping = {k: v for k, v in mapping.items() if k != "_default_"}
        type_names = [t for t in type_names if t != "_default_"]

    if not type_names:
        return mapping

    report.flattened_types.extend(type_names)
    if len(type_names) > 1:
        report.notes.append("flattened multiple types into one typeless mapping; conflicts listed")

    base_type = type_names[0]
    base = dict(mapping[base_type])
    base_props = dict(base.get("properties") or {})

    for t in type_names[1:]:
        other = mapping[t]
        other_props = (other.get("properties") or {}) if isinstance(other, dict) else {}
        for fname, fdef in other_props.items():
            if fname in base_props and base_props[fname] != fdef:
                report.type_conflicts.append(f"{fname}: types {base_type} vs {t}")
                # Keep the first occurrence (base) — operator must reconcile.
                continue
            base_props[fname] = fdef
        # Merge top-level options (e.g. dynamic, _meta) — first writer wins.
        if isinstance(other, dict):
            for k, v in other.items():
                if k == "properties":
                    continue
                base.setdefault(k, v)

    base["properties"] = base_props
    # Preserve any reserved top-level keys that were *outside* the type wrapper.
    for k, v in mapping.items():
        if k in _RESERVED_MAPPING_KEYS:
            base.setdefault(k, v)
    return base


def _sanitize_field(
    field_name: str,
    field_def: Dict[str, Any],
    path: str,
    report: SanitizationReport,
) -> Optional[Dict[str, Any]]:
    """Return a sanitized copy of one field definition."""
    if not isinstance(field_def, dict):
        return field_def

    out: Dict[str, Any] = {}
    for key, value in field_def.items():
        if key in _DEPRECATED_FIELD_OPTIONS:
            report.removed_mapping_fields.append(f"{path}.{key}")
            continue

        # `string` type → `text`/`keyword`
        if key == "type" and value == "string":
            indexing = field_def.get("index")
            if indexing == "not_analyzed":
                # ES 5.x "not_analyzed string" → keyword
                out["type"] = "keyword"
                report.rewrote_mapping_fields.append(f"{path}.type=string→keyword")
                # Drop the now-meaningless `index` value.
                continue
            if indexing == "no":
                out["type"] = "keyword"
                out["index"] = False
                report.rewrote_mapping_fields.append(f"{path}.type=string→keyword(index=false)")
                continue
            # Default: text + a keyword multi-field for sort/agg parity.
            out["type"] = "text"
            existing_fields = field_def.get("fields") or {}
            if "keyword" not in existing_fields:
                existing_fields = dict(existing_fields)
                existing_fields["keyword"] = {"type": "keyword", "ignore_above": 256}
            out["fields"] = existing_fields
            report.rewrote_mapping_fields.append(f"{path}.type=string→text+keyword")
            continue

        if key == "index" and isinstance(value, str):
            # ES 6+: index is bool. Map "not_analyzed"/"no"/"analyzed" → bool.
            if value in ("not_analyzed", "analyzed"):
                # Only meaningful when type=string was rewritten above; drop.
                report.removed_mapping_fields.append(f"{path}.index={value!r}")
                continue
            if value == "no":
                out["index"] = False
                report.rewrote_mapping_fields.append(f"{path}.index='no'→False")
                continue

        if key == "properties" and isinstance(value, dict):
            out["properties"] = {
                f: _sanitize_field(f, sub, f"{path}.{f}", report) for f, sub in value.items()
            }
            continue

        if key == "fields" and isinstance(value, dict):
            out["fields"] = {
                f: _sanitize_field(f, sub, f"{path}.fields.{f}", report) for f, sub in value.items()
            }
            continue

        out[key] = value

    return out


def sanitize_mapping(
    mapping: Dict[str, Any],
    source_version: str = SOURCE_VERSION_AUTODETECT,
    target_type: str = TARGET_ELASTICSEARCH_SERVERLESS,
) -> Tuple[Dict[str, Any], SanitizationReport]:
    """Sanitize an index mapping for the *target_type* destination.

    *mapping* may be:
      * The full ``"mappings"`` block from a template / index settings, or
      * A bare type body (``{"properties": {...}}``).

    Returns a sanitized typeless mapping shaped like ``{"properties": {...}}``
    plus any preserved reserved keys, alongside a :class:`SanitizationReport`.
    """
    if target_type not in TARGET_TYPES:
        raise ValueError(f"unknown target_type: {target_type!r}")

    report = SanitizationReport()
    if not isinstance(mapping, dict):
        return mapping, report

    # Strip whole-mapping deprecated options (e.g. _all).
    cleaned = {}
    for k, v in mapping.items():
        if k in _DEPRECATED_TOP_LEVEL_OPTIONS:
            report.removed_mapping_fields.append(k)
            continue
        cleaned[k] = v

    # Multi-type detection / flatten.
    type_names = _has_multiple_types(cleaned)
    if type_names:
        cleaned = _flatten_multi_type(cleaned, report)

    # Recurse into properties.
    out: Dict[str, Any] = {}
    props = cleaned.get("properties")
    if isinstance(props, dict):
        out["properties"] = {
            f: _sanitize_field(f, sub, f"properties.{f}", report) for f, sub in props.items()
        }

    # Preserve safe top-level keys.
    for k in (
        "_meta",
        "_source",
        "_routing",
        "_field_names",
        "_size",
        "dynamic",
        "dynamic_templates",
        "date_detection",
        "numeric_detection",
        "dynamic_date_formats",
        "runtime",
        "subobjects",
        "enabled",
    ):
        if k in cleaned:
            out[k] = cleaned[k]

    return out, report


# ---------------------------------------------------------------------------
# Combined helper used by the migrator and by callers preparing destination
# index bodies (e.g. before s3_bulk_load.py).
# ---------------------------------------------------------------------------


def sanitize_index_body(
    body: Dict[str, Any],
    source_version: str = SOURCE_VERSION_AUTODETECT,
    target_type: str = TARGET_ELASTICSEARCH_SERVERLESS,
) -> Tuple[Dict[str, Any], SanitizationReport]:
    """Sanitize a full ``settings + mappings + aliases`` body (the shape a
    PUT-create-index or template request takes).
    """
    out: Dict[str, Any] = dict(body)
    report = SanitizationReport()

    if "settings" in out and isinstance(out["settings"], dict):
        new_settings, settings_report = sanitize_index_settings(out["settings"], target_type)
        out["settings"] = new_settings
        report = report.merge(settings_report)

    if "mappings" in out and isinstance(out["mappings"], dict):
        new_mappings, mapping_report = sanitize_mapping(
            out["mappings"], source_version, target_type
        )
        out["mappings"] = new_mappings
        report = report.merge(mapping_report)

    return out, report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_json(path: Optional[str]) -> Any:
    if not path or path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: Optional[str], data: Any) -> None:
    text = json.dumps(data, indent=2, sort_keys=False)
    if not path or path == "-":
        sys.stdout.write(text + "\n")
        return
    Path(path).write_text(text + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Sanitize an index mapping or settings body for a destination Elastic "
            "(default: ELASTICSEARCH_SERVERLESS). Reads JSON from --input or stdin "
            "and writes the sanitized body and a report."
        ),
    )
    p.add_argument("--input", default="-", help="Input JSON file (default: stdin).")
    p.add_argument("--output", default="-", help="Output JSON file (default: stdout).")
    p.add_argument(
        "--report",
        default=None,
        help="Optional path to write the sanitization report as JSON.",
    )
    p.add_argument(
        "--target-type",
        default=TARGET_ELASTICSEARCH_SERVERLESS,
        choices=TARGET_TYPES,
    )
    p.add_argument(
        "--source-version",
        default=SOURCE_VERSION_AUTODETECT,
        help="Hint the source version (e.g. Elasticsearch_5_6). Default autodetects.",
    )
    p.add_argument(
        "--mode",
        choices=("settings", "mapping", "index-body"),
        default="index-body",
        help=(
            "What kind of JSON the input is. 'index-body' covers "
            "{settings, mappings, aliases} as used by PUT /idx and templates."
        ),
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if the sanitizer made any changes.",
    )
    args = p.parse_args(argv)

    raw = _read_json(args.input)
    if args.mode == "settings":
        out, report = sanitize_index_settings(raw, args.target_type)
    elif args.mode == "mapping":
        out, report = sanitize_mapping(raw, args.source_version, args.target_type)
    else:
        out, report = sanitize_index_body(raw, args.source_version, args.target_type)

    _write_json(args.output, out)
    if args.report:
        Path(args.report).write_text(
            json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8"
        )

    return 1 if (args.strict and not report.is_empty()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
