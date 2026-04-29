"""Tests for ``metadata_migration.sanitizer``.

Pure-Python helpers; no network or moto needed.
"""

from __future__ import annotations

from metadata_migration.sanitizer import (
    TARGET_ELASTICSEARCH,
    TARGET_ELASTICSEARCH_SERVERLESS,
    sanitize_index_body,
    sanitize_index_settings,
    sanitize_mapping,
)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_serverless_strips_shards_replicas_translog() -> None:
    settings = {
        "index": {
            "number_of_shards": 5,
            "number_of_replicas": 1,
            "refresh_interval": "1s",
            "translog": {"durability": "request"},
            "merge": {"policy": {"max_merged_segment": "5gb"}},
        }
    }
    out, report = sanitize_index_settings(settings, TARGET_ELASTICSEARCH_SERVERLESS)
    # refresh_interval is allowed; the rest should be gone.
    assert out == {"index": {"refresh_interval": "1s"}}
    assert "index.number_of_shards" in report.removed_settings
    assert "index.number_of_replicas" in report.removed_settings
    assert any(p.startswith("index.translog") for p in report.removed_settings)
    assert any(p.startswith("index.merge") for p in report.removed_settings)


def test_hosted_keeps_shards_strips_only_opensearch_only() -> None:
    settings = {
        "index": {
            "number_of_shards": 3,
            "knn": True,
            "replication": {"type": "SEGMENT"},
        }
    }
    out, _ = sanitize_index_settings(settings, TARGET_ELASTICSEARCH)
    # Hosted retains shards/replicas but the OpenSearch-only knobs go.
    assert out["index"]["number_of_shards"] == 3
    assert "knn" not in out["index"]
    assert "replication" not in out["index"]


def test_settings_handles_flat_input() -> None:
    settings = {"index.number_of_shards": 5, "index.refresh_interval": "30s"}
    out, report = sanitize_index_settings(settings, TARGET_ELASTICSEARCH_SERVERLESS)
    assert out == {"index": {"refresh_interval": "30s"}}
    assert "index.number_of_shards" in report.removed_settings


# ---------------------------------------------------------------------------
# Mapping — ES 5.x multi-type flattening
# ---------------------------------------------------------------------------


def test_multi_type_flattens_into_typeless() -> None:
    mapping = {
        "user": {
            "_all": {"enabled": False},  # nested deprecated key, sanitized at a deeper layer
            "properties": {
                "name": {"type": "string", "index": "not_analyzed"},
                "email": {"type": "string"},
            },
        },
        "post": {
            "properties": {
                "title": {"type": "string"},
                "name": {"type": "string"},  # conflict with user.name
            }
        },
        "_default_": {"properties": {"common": {"type": "string"}}},
    }
    out, report = sanitize_mapping(mapping, target_type=TARGET_ELASTICSEARCH_SERVERLESS)
    assert "user" in report.flattened_types
    assert "post" in report.flattened_types
    # _default_ noted, not flattened.
    assert any("_default_" in n for n in report.notes)
    # properties merged; name took the first writer (user/keyword), conflict reported for post.
    props = out["properties"]
    assert props["name"]["type"] == "keyword"  # was "string"+not_analyzed
    assert props["email"]["type"] == "text"  # was string default → text+keyword multi
    assert "keyword" in props["email"]["fields"]
    assert props["title"]["type"] == "text"
    assert any(c.startswith("name:") for c in report.type_conflicts)


def test_drops_top_level_all_and_default() -> None:
    mapping = {
        "_all": {"enabled": False},
        "_timestamp": {"enabled": True},
        "properties": {"x": {"type": "integer"}},
    }
    out, report = sanitize_mapping(mapping, target_type=TARGET_ELASTICSEARCH_SERVERLESS)
    assert "_all" not in out
    assert "_timestamp" not in out
    assert out["properties"]["x"]["type"] == "integer"
    assert "_all" in report.removed_mapping_fields
    assert "_timestamp" in report.removed_mapping_fields


def test_string_to_text_with_keyword_multifield() -> None:
    mapping = {"properties": {"msg": {"type": "string"}}}
    out, report = sanitize_mapping(mapping, target_type=TARGET_ELASTICSEARCH_SERVERLESS)
    msg = out["properties"]["msg"]
    assert msg["type"] == "text"
    assert msg["fields"]["keyword"]["type"] == "keyword"
    assert any("string→text+keyword" in r for r in report.rewrote_mapping_fields)


def test_string_not_analyzed_to_keyword() -> None:
    mapping = {"properties": {"id": {"type": "string", "index": "not_analyzed"}}}
    out, _ = sanitize_mapping(mapping, target_type=TARGET_ELASTICSEARCH_SERVERLESS)
    assert out["properties"]["id"]["type"] == "keyword"
    assert "index" not in out["properties"]["id"]


def test_drops_deprecated_field_options() -> None:
    mapping = {
        "properties": {
            "x": {"type": "text", "include_in_all": True, "boost": 2.0, "norms": {"enabled": True}}
        }
    }
    out, report = sanitize_mapping(mapping, target_type=TARGET_ELASTICSEARCH_SERVERLESS)
    assert "include_in_all" not in out["properties"]["x"]
    assert "boost" not in out["properties"]["x"]
    assert any("include_in_all" in r for r in report.removed_mapping_fields)


def test_nested_properties_sanitized() -> None:
    mapping = {
        "properties": {
            "user": {
                "properties": {
                    "name": {"type": "string", "index": "not_analyzed"},
                    "tags": {"type": "string"},
                }
            }
        }
    }
    out, _ = sanitize_mapping(mapping, target_type=TARGET_ELASTICSEARCH_SERVERLESS)
    user_props = out["properties"]["user"]["properties"]
    assert user_props["name"]["type"] == "keyword"
    assert user_props["tags"]["type"] == "text"
    assert "keyword" in user_props["tags"]["fields"]


# ---------------------------------------------------------------------------
# Combined index body
# ---------------------------------------------------------------------------


def test_index_body_combines_settings_and_mapping_reports() -> None:
    body = {
        "settings": {
            "index": {
                "number_of_shards": 1,
                "refresh_interval": "1s",
                "knn": True,
            }
        },
        "mappings": {
            "_all": {"enabled": False},
            "properties": {"id": {"type": "string", "index": "not_analyzed"}},
        },
        "aliases": {"my-alias": {}},
    }
    out, report = sanitize_index_body(body, target_type=TARGET_ELASTICSEARCH_SERVERLESS)
    assert out["settings"] == {"index": {"refresh_interval": "1s"}}
    assert "_all" not in out["mappings"]
    assert out["mappings"]["properties"]["id"]["type"] == "keyword"
    # aliases passthrough
    assert out["aliases"] == {"my-alias": {}}
    assert any(p == "index.number_of_shards" for p in report.removed_settings)
    assert any(p == "index.knn" for p in report.removed_settings)
    assert "_all" in report.removed_mapping_fields


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_passthrough_no_changes_returns_zero(tmp_path, capsys) -> None:
    from metadata_migration import sanitizer

    in_path = tmp_path / "in.json"
    out_path = tmp_path / "out.json"
    in_path.write_text('{"settings": {"index": {"refresh_interval": "1s"}}}')
    rc = sanitizer.main(
        [
            "--input",
            str(in_path),
            "--output",
            str(out_path),
            "--mode",
            "index-body",
            "--target-type",
            "ELASTICSEARCH_SERVERLESS",
            "--strict",
        ]
    )
    assert rc == 0
    assert out_path.read_text().strip().startswith("{")


def test_cli_strict_returns_one_when_changes(tmp_path) -> None:
    from metadata_migration import sanitizer

    in_path = tmp_path / "in.json"
    in_path.write_text('{"settings": {"index": {"number_of_shards": 5}}}')
    rc = sanitizer.main(
        [
            "--input",
            str(in_path),
            "--output",
            "-",
            "--mode",
            "index-body",
            "--strict",
        ]
    )
    assert rc == 1
