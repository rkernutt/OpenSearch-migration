"""End-to-end tests for ``metadata_migration.migrator``.

Monkeypatches the source GET helpers and the destination PUT/HEAD helpers so
no real cluster is needed.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from metadata_migration import migrator

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeSource:
    def __init__(self, payloads: Dict[str, Dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls: List[str] = []

    def get(self, host: str, path: str, auth) -> Dict[str, Any]:
        self.calls.append(path)
        return self.payloads.get(path, {})


class _FakeDest:
    """Records puts and head requests; head returns 404 by default."""

    def __init__(self, existing: Dict[str, int] | None = None) -> None:
        self.existing = existing or {}
        self.puts: List[Dict[str, Any]] = []
        self.heads: List[str] = []

    def put(self, host: str, path: str, body: Dict[str, Any], dest_auth):
        self.puts.append({"path": path, "body": body})

        class R:
            status_code = 200
            text = ""

        return R()

    def head(self, host: str, path: str, dest_auth) -> int:
        self.heads.append(path)
        return self.existing.get(path, 404)


def _patch(monkeypatch, source: _FakeSource, dest: _FakeDest) -> None:
    monkeypatch.setattr(migrator, "_get_source", source.get)
    monkeypatch.setattr(migrator, "_put_dest", dest.put)
    monkeypatch.setattr(migrator, "_head_dest", dest.head)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _common_args() -> List[str]:
    return [
        "--source-host",
        "https://source.example",
        "--source-user",
        "u",
        "--source-password",
        "p",
        "--dest-host",
        "https://dest.example",
        "--dest-api-key",
        "id:secret",
        "--strict-exit-codes",
        "--log-format",
        "json",
    ]


def test_legacy_template_migrated_with_settings_sanitization(monkeypatch, capsys) -> None:
    source = _FakeSource(
        {
            "/_template": {
                "logs-template": {
                    "index_patterns": ["logs-*"],
                    "settings": {"index": {"number_of_shards": 5, "refresh_interval": "1s"}},
                    "mappings": {"properties": {"x": {"type": "string"}}},
                }
            }
        }
    )
    dest = _FakeDest()
    _patch(monkeypatch, source, dest)

    rc = migrator.main(_common_args() + ["--include", "templates"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["by_status"].get("ok") == 1

    assert len(dest.puts) == 1
    sent = dest.puts[0]
    assert sent["path"] == "/_template/logs-template"
    assert sent["body"]["settings"] == {"index": {"refresh_interval": "1s"}}
    assert sent["body"]["mappings"]["properties"]["x"]["type"] == "text"


def test_index_template_inner_template_sanitized(monkeypatch) -> None:
    source = _FakeSource(
        {
            "/_index_template": {
                "index_templates": [
                    {
                        "name": "logs-it",
                        "index_template": {
                            "index_patterns": ["logs-*"],
                            "template": {
                                "settings": {
                                    "index": {
                                        "number_of_shards": 1,
                                        "knn": True,
                                        "refresh_interval": "10s",
                                    }
                                },
                                "mappings": {
                                    "properties": {
                                        "id": {"type": "string", "index": "not_analyzed"}
                                    }
                                },
                            },
                            "priority": 50,
                        },
                    }
                ]
            }
        }
    )
    dest = _FakeDest()
    _patch(monkeypatch, source, dest)

    rc = migrator.main(_common_args() + ["--include", "index_templates"])
    assert rc == 0
    sent = dest.puts[0]["body"]
    assert sent["priority"] == 50  # passthrough
    assert sent["template"]["settings"] == {"index": {"refresh_interval": "10s"}}
    assert sent["template"]["mappings"]["properties"]["id"]["type"] == "keyword"


def test_existing_object_skipped_unless_overwrite(monkeypatch) -> None:
    source = _FakeSource(
        {
            "/_template": {
                "logs": {
                    "index_patterns": ["logs-*"],
                    "settings": {},
                    "mappings": {"properties": {}},
                }
            }
        }
    )
    dest = _FakeDest(existing={"/_template/logs": 200})
    _patch(monkeypatch, source, dest)

    rc = migrator.main(_common_args() + ["--include", "templates"])
    assert rc == 0
    assert dest.puts == []  # no PUT — already exists

    # With --overwrite it should write through.
    dest2 = _FakeDest(existing={"/_template/logs": 200})
    _patch(monkeypatch, source, dest2)
    rc = migrator.main(_common_args() + ["--include", "templates", "--overwrite"])
    assert rc == 0
    assert len(dest2.puts) == 1


def test_dry_run_does_not_put(monkeypatch, capsys) -> None:
    source = _FakeSource(
        {
            "/_template": {
                "x": {
                    "index_patterns": ["x-*"],
                    "settings": {"index": {"number_of_shards": 3}},
                    "mappings": {"properties": {}},
                }
            }
        }
    )
    dest = _FakeDest()
    _patch(monkeypatch, source, dest)

    rc = migrator.main(_common_args() + ["--include", "templates", "--dry-run"])
    assert rc == 0
    assert dest.puts == []
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["by_status"].get("dry-run") == 1
    assert summary["dry_run"] is True


def test_system_objects_skipped_by_default(monkeypatch) -> None:
    source = _FakeSource(
        {
            "/_template": {
                ".kibana_template": {"index_patterns": [".kibana*"]},
                "logs-template": {
                    "index_patterns": ["logs-*"],
                    "settings": {},
                    "mappings": {"properties": {}},
                },
            }
        }
    )
    dest = _FakeDest()
    _patch(monkeypatch, source, dest)

    rc = migrator.main(_common_args() + ["--include", "templates"])
    assert rc == 0
    paths = [p["path"] for p in dest.puts]
    assert paths == ["/_template/logs-template"]


def test_name_filter_applies(monkeypatch) -> None:
    source = _FakeSource(
        {
            "/_template": {
                "logs-template": {
                    "index_patterns": ["logs-*"],
                    "settings": {},
                    "mappings": {"properties": {}},
                },
                "metrics-template": {
                    "index_patterns": ["metrics-*"],
                    "settings": {},
                    "mappings": {"properties": {}},
                },
            }
        }
    )
    dest = _FakeDest()
    _patch(monkeypatch, source, dest)

    rc = migrator.main(
        _common_args() + ["--include", "templates", "--name", "logs-*", "--exclude", "*-deprecated"]
    )
    assert rc == 0
    assert [p["path"] for p in dest.puts] == ["/_template/logs-template"]


def test_failed_put_returns_object_failures_exit_code(monkeypatch) -> None:
    source = _FakeSource(
        {
            "/_template": {
                "good": {
                    "index_patterns": ["good-*"],
                    "settings": {},
                    "mappings": {"properties": {}},
                },
                "bad": {
                    "index_patterns": ["bad-*"],
                    "settings": {},
                    "mappings": {"properties": {}},
                },
            }
        }
    )

    class _MixedDest(_FakeDest):
        def put(self, host, path, body, dest_auth):
            self.puts.append({"path": path, "body": body})

            class R:
                pass

            r = R()
            if path.endswith("/bad"):
                r.status_code = 400
                r.text = '{"error":"boom"}'
            else:
                r.status_code = 200
                r.text = ""
            return r

    dest = _MixedDest()
    _patch(monkeypatch, source, dest)
    rc = migrator.main(_common_args() + ["--include", "templates"])
    assert rc == migrator.EXIT_OBJECT_FAILURES


def test_ingest_pipeline_passthrough_strips_opensearch_meta(monkeypatch) -> None:
    source = _FakeSource(
        {
            "/_ingest/pipeline": {
                "logs-enrich": {
                    "description": "enrich",
                    "processors": [{"set": {"field": "src", "value": "x"}}],
                    "opendistro_meta": {"foo": "bar"},
                }
            }
        }
    )
    dest = _FakeDest()
    _patch(monkeypatch, source, dest)
    rc = migrator.main(_common_args() + ["--include", "ingest_pipelines"])
    assert rc == 0
    sent = dest.puts[0]["body"]
    assert "opendistro_meta" not in sent
    assert sent["processors"][0]["set"]["field"] == "src"
