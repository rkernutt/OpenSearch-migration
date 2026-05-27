"""Unit + integration tests for ``compat_check.py``.

Pure-function tests run without any HTTP. The end-to-end tests use a
tiny fake :class:`requests.Session` that returns canned JSON for
``/``, ``/_cat/indices``, ``/{index}/_settings`` and ``/{index}/_mapping``.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import compat_check

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_parse_major_handles_invalid() -> None:
    assert compat_check._parse_major("8.13.4") == 8
    assert compat_check._parse_major("not-a-version") == 0
    assert compat_check._parse_major("") == 0


def test_max_severity_orders_correctly() -> None:
    assert compat_check._max_severity("ok", "warn") == "warn"
    assert compat_check._max_severity("warn", "block-rfs") == "block-rfs"
    assert compat_check._max_severity("block-rfs", "warn") == "block-rfs"
    assert compat_check._max_severity("ok", "ok") == "ok"


def test_flatten_settings_dots_nested_keys() -> None:
    nested = {"index": {"number_of_shards": "1", "knn": "true"}}
    flat = compat_check._flatten_settings(nested)
    assert flat == {"index.number_of_shards": "1", "index.knn": "true"}


def test_inspect_mapping_detects_string_type() -> None:
    mapping = {"properties": {"title": {"type": "string"}}}
    flags = compat_check._inspect_mapping(mapping)
    assert "legacy-string-type" in flags


def test_inspect_mapping_detects_multi_type() -> None:
    mapping = {"user": {"properties": {}}, "tweet": {"properties": {}}}
    flags = compat_check._inspect_mapping(mapping)
    assert any(f.startswith("multi-type-mapping") for f in flags)


def test_inspect_mapping_detects_deprecated_options() -> None:
    mapping = {"_all": {"enabled": False}, "properties": {}}
    flags = compat_check._inspect_mapping(mapping)
    assert "deprecated-option=_all" in flags


def test_inspect_mapping_clean_returns_empty() -> None:
    mapping = {"properties": {"title": {"type": "text"}}}
    assert compat_check._inspect_mapping(mapping) == []


# ---------------------------------------------------------------------------
# Cluster warnings
# ---------------------------------------------------------------------------


def _src(version: str, lucene: str, distribution: str = "opensearch") -> compat_check.ClusterInfo:
    return compat_check.ClusterInfo(
        host="https://src",
        distribution=distribution,
        version=version,
        major=compat_check._parse_major(version),
        lucene_version=lucene,
        lucene_major=compat_check._parse_major(lucene),
        build_flavor=None,
    )


def _dst(version: str, lucene: str, flavor: str | None = None) -> compat_check.ClusterInfo:
    return compat_check.ClusterInfo(
        host="https://dst",
        distribution="elasticsearch",
        version=version,
        major=compat_check._parse_major(version),
        lucene_version=lucene,
        lucene_major=compat_check._parse_major(lucene),
        build_flavor=flavor,
    )


def test_cluster_warnings_serverless_destination_flagged() -> None:
    warnings = compat_check.cluster_warnings(
        _src("2.13.0", "9.10.0"),
        _dst("8.15.0", "9.10.0", flavor="serverless"),
    )
    assert any("Serverless" in w for w in warnings)


def test_cluster_warnings_lucene_too_new_for_destination() -> None:
    # OS 3.x (Lucene 10) -> ES 8.x (Lucene 9) can't read the segments.
    warnings = compat_check.cluster_warnings(
        _src("3.0.0", "10.0.0"),
        _dst("8.15.0", "9.10.0"),
    )
    assert any("requires ES" in w for w in warnings)


def test_cluster_warnings_clean_when_lucene_fits() -> None:
    # OS 2.x (Lucene 9) -> ES 8.x (Lucene 9) is the happy path.
    warnings = compat_check.cluster_warnings(
        _src("2.13.0", "9.10.0"),
        _dst("8.15.0", "9.10.0"),
    )
    assert warnings == []


def test_cluster_warnings_os3_emits_pilot_advice_without_dest() -> None:
    warnings = compat_check.cluster_warnings(_src("3.0.0", "10.0.0"), None)
    assert any("OpenSearch 3.x" in w for w in warnings)


# ---------------------------------------------------------------------------
# Fake-session integration
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.text = json.dumps(payload)
        self.response = None

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeSession:
    """Routes ``GET <host><path>`` to a handler dict."""

    def __init__(self, handlers: dict[str, Callable[[], Any]]) -> None:
        self._handlers = handlers

    def get(
        self,
        url: str,
        *,
        params: Any = None,
        headers: Any = None,
        auth: Any = None,
        timeout: Any = None,
    ) -> _FakeResponse:
        # Strip host prefix.
        path = url.split("/", 3)[-1] if url.startswith("http") else url
        if path in self._handlers:
            return _FakeResponse(self._handlers[path]())
        raise AssertionError(f"unexpected GET {url} (path={path}, known={sorted(self._handlers)})")


def _settings_payload(index: str, **settings: str) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for k, v in settings.items():
        # turn dotted keys into nested
        cursor: Any = flat
        parts = k.split(".")
        for p in parts[:-1]:
            cursor = cursor.setdefault(p, {})
        cursor[parts[-1]] = v
    return {index: {"settings": flat}}


def _mapping_payload(index: str, mapping: dict[str, Any]) -> dict[str, Any]:
    return {index: {"mappings": mapping}}


def test_inspect_index_flags_knn() -> None:
    session = _FakeSession(
        {
            "logs/_settings": lambda: _settings_payload("logs", **{"index.knn": "true"}),
            "logs/_mapping": lambda: _mapping_payload(
                "logs", {"properties": {"vec": {"type": "knn_vector", "dimension": 4}}}
            ),
        }
    )
    finding = compat_check.inspect_index(
        "https://src",
        "logs",
        headers={},
        auth=None,
        session=session,  # type: ignore[arg-type]
        timeout=5.0,
        target_type=None,
    )
    assert finding.severity == "block-rfs"
    assert "index.knn=true" in finding.settings_flags
    assert any("k-NN" in i for i in finding.issues)


def test_inspect_index_flags_opensearch_codec() -> None:
    session = _FakeSession(
        {
            "logs/_settings": lambda: _settings_payload("logs", **{"index.codec": "zstd_no_dict"}),
            "logs/_mapping": lambda: _mapping_payload(
                "logs", {"properties": {"msg": {"type": "text"}}}
            ),
        }
    )
    finding = compat_check.inspect_index(
        "https://src",
        "logs",
        headers={},
        auth=None,
        session=session,  # type: ignore[arg-type]
        timeout=5.0,
        target_type=None,
    )
    assert finding.severity == "block-rfs"
    assert "index.codec=zstd_no_dict" in finding.settings_flags


def test_inspect_index_clean_index_returns_ok() -> None:
    session = _FakeSession(
        {
            "logs/_settings": lambda: _settings_payload("logs", **{"index.number_of_shards": "1"}),
            "logs/_mapping": lambda: _mapping_payload(
                "logs", {"properties": {"msg": {"type": "text"}}}
            ),
        }
    )
    finding = compat_check.inspect_index(
        "https://src",
        "logs",
        headers={},
        auth=None,
        session=session,  # type: ignore[arg-type]
        timeout=5.0,
        target_type=None,
    )
    assert finding.severity == "ok"
    assert finding.issues == []


def test_inspect_index_serverless_forbidden_setting_warns() -> None:
    session = _FakeSession(
        {
            "logs/_settings": lambda: _settings_payload(
                "logs",
                **{
                    "index.number_of_shards": "3",
                    "index.refresh_interval": "30s",
                },
            ),
            "logs/_mapping": lambda: _mapping_payload(
                "logs", {"properties": {"msg": {"type": "text"}}}
            ),
        }
    )
    finding = compat_check.inspect_index(
        "https://src",
        "logs",
        headers={},
        auth=None,
        session=session,  # type: ignore[arg-type]
        timeout=5.0,
        target_type="ELASTICSEARCH_SERVERLESS",
    )
    assert finding.severity == "warn"
    assert any("Serverless" in i for i in finding.issues)


# ---------------------------------------------------------------------------
# Report aggregation
# ---------------------------------------------------------------------------


def test_report_has_warnings_when_any_index_flagged() -> None:
    src = _src("2.13.0", "9.10.0")
    findings = [
        compat_check.IndexFinding(
            index="ok-index", severity="ok", issues=[], settings_flags=[], mapping_flags=[]
        ),
        compat_check.IndexFinding(
            index="knn-index",
            severity="block-rfs",
            issues=["k-NN"],
            settings_flags=["index.knn=true"],
            mapping_flags=[],
        ),
    ]
    report = compat_check.Report(source=src, dest=None, cluster_warnings=[], indices=findings)
    assert report.has_warnings is True
    summary = report.to_json()["summary"]
    assert summary["ok"] == 1
    assert summary["block_rfs"] == 1


def test_report_clean_when_all_ok() -> None:
    src = _src("2.13.0", "9.10.0")
    findings = [
        compat_check.IndexFinding(
            index="ok-index", severity="ok", issues=[], settings_flags=[], mapping_flags=[]
        )
    ]
    report = compat_check.Report(source=src, dest=None, cluster_warnings=[], indices=findings)
    assert report.has_warnings is False
