"""End-to-end tests for ``shadow_diff.py``.

We monkeypatch ``shadow_diff._post_search`` so we don't talk to a real
cluster. The tests cover the full pass/fail decision matrix.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

import shadow_diff


def _hits(
    total: int, ids: List[str], sources: List[Dict[str, Any]] | None = None
) -> Dict[str, Any]:
    sources = sources or [{"_id": i} for i in ids]
    return {
        "hits": {
            "total": {"value": total, "relation": "eq"},
            "hits": [{"_id": i, "_source": s} for i, s in zip(ids, sources)],
        }
    }


def _patch(monkeypatch, plan: Dict[Tuple[str, str], Tuple[int, Dict[str, Any]]]) -> None:
    """Plan: {(endpoint_label, query_name): (status, body)}."""

    def fake_post(ep, q):
        key = (ep.label, q.name)
        if key not in plan:
            raise AssertionError(f"unexpected key {key}")
        st, body = plan[key]
        return st, body, ""

    monkeypatch.setattr(shadow_diff, "_post_search", fake_post)


def _common_args(tmp_path, queries: List[Dict[str, Any]]) -> List[str]:
    qfile = tmp_path / "queries.json"
    qfile.write_text(json.dumps(queries))
    return [
        "--source-host",
        "https://src.example",
        "--source-user",
        "u",
        "--source-password",
        "p",
        "--dest-host",
        "https://dst.example",
        "--dest-api-key",
        "id:secret",
        "--queries-file",
        str(qfile),
        "--strict-exit-codes",
        "--log-format",
        "json",
        "--workers",
        "1",
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pass_when_results_match(monkeypatch, capsys, tmp_path) -> None:
    queries = [{"name": "q1", "index": "logs", "body": {"query": {"match_all": {}}}}]
    _patch(
        monkeypatch,
        {
            ("source", "q1"): (200, _hits(10, ["a", "b", "c"], [{"x": 1}] * 3)),
            ("dest", "q1"): (200, _hits(10, ["a", "b", "c"], [{"x": 1}] * 3)),
        },
    )
    rc = shadow_diff.main(_common_args(tmp_path, queries))
    assert rc == shadow_diff.EXIT_OK
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["queries_passed"] == 1
    assert summary["queries_drifted"] == 0


def test_count_drift_fails(monkeypatch, capsys, tmp_path) -> None:
    queries = [{"name": "q1", "index": "logs", "body": {}}]
    _patch(
        monkeypatch,
        {
            ("source", "q1"): (200, _hits(100, ["a"])),
            ("dest", "q1"): (200, _hits(50, ["a"])),
        },
    )
    rc = shadow_diff.main(_common_args(tmp_path, queries))
    assert rc == shadow_diff.EXIT_DRIFT
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    drifts = summary["drift_by_name"]
    assert len(drifts) == 1
    assert any(f.startswith("count") for f in drifts[0]["failures"])


def test_count_within_tolerance_passes(monkeypatch, tmp_path) -> None:
    queries = [{"name": "q1", "index": "logs", "body": {}}]
    _patch(
        monkeypatch,
        {
            ("source", "q1"): (200, _hits(100, ["a", "b"])),
            ("dest", "q1"): (200, _hits(105, ["a", "b"])),
        },
    )
    rc = shadow_diff.main(_common_args(tmp_path, queries) + ["--count-tolerance", "0.1"])
    assert rc == shadow_diff.EXIT_OK


def test_topk_ids_drift_fails(monkeypatch, tmp_path, capsys) -> None:
    queries = [{"name": "q1", "index": "logs", "body": {}}]
    _patch(
        monkeypatch,
        {
            ("source", "q1"): (200, _hits(3, ["a", "b", "c"])),
            ("dest", "q1"): (200, _hits(3, ["x", "y", "z"])),
        },
    )
    rc = shadow_diff.main(_common_args(tmp_path, queries))
    assert rc == shadow_diff.EXIT_DRIFT
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    drift = summary["drift_by_name"][0]
    assert any(f.startswith("topk-ids") for f in drift["failures"])
    assert drift["metrics"]["topk_jaccard"] == 0.0


def test_topk_id_threshold_relaxed_passes(monkeypatch, tmp_path) -> None:
    queries = [{"name": "q1", "index": "logs", "body": {}}]
    _patch(
        monkeypatch,
        {
            ("source", "q1"): (200, _hits(3, ["a", "b", "c"])),
            ("dest", "q1"): (200, _hits(3, ["a", "b", "z"])),
        },
    )
    # 2/4 = 0.5 jaccard. Threshold 0.5 should pass.
    rc = shadow_diff.main(
        _common_args(tmp_path, queries) + ["--topk-id-threshold", "0.5", "--no-hashes"]
    )
    assert rc == shadow_diff.EXIT_OK


def test_hash_drift_fails_even_when_ids_match(monkeypatch, capsys, tmp_path) -> None:
    queries = [{"name": "q1", "index": "logs", "body": {}}]
    _patch(
        monkeypatch,
        {
            ("source", "q1"): (200, _hits(2, ["a", "b"], [{"v": 1}, {"v": 2}])),
            ("dest", "q1"): (200, _hits(2, ["a", "b"], [{"v": 1}, {"v": 999}])),
        },
    )
    rc = shadow_diff.main(_common_args(tmp_path, queries))
    assert rc == shadow_diff.EXIT_DRIFT
    drift = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["drift_by_name"][0]
    assert any(f.startswith("topk-hashes") for f in drift["failures"])


def test_no_hashes_skips_hash_check(monkeypatch, tmp_path) -> None:
    queries = [{"name": "q1", "index": "logs", "body": {}}]
    _patch(
        monkeypatch,
        {
            ("source", "q1"): (200, _hits(2, ["a", "b"], [{"v": 1}, {"v": 2}])),
            ("dest", "q1"): (200, _hits(2, ["a", "b"], [{"v": 1}, {"v": 999}])),
        },
    )
    rc = shadow_diff.main(_common_args(tmp_path, queries) + ["--no-hashes"])
    assert rc == shadow_diff.EXIT_OK


def test_status_mismatch_fails(monkeypatch, capsys, tmp_path) -> None:
    queries = [{"name": "q1", "index": "logs", "body": {}}]
    _patch(
        monkeypatch,
        {
            ("source", "q1"): (200, _hits(1, ["a"])),
            ("dest", "q1"): (404, {}),
        },
    )
    rc = shadow_diff.main(_common_args(tmp_path, queries))
    assert rc == shadow_diff.EXIT_DRIFT
    drift = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["drift_by_name"][0]
    assert "status" in drift["failures"]


def test_queries_dir_loads_multiple_files(monkeypatch, tmp_path) -> None:
    qdir = tmp_path / "queries"
    qdir.mkdir()
    (qdir / "first.json").write_text(json.dumps({"name": "first", "index": "i", "body": {}}))
    (qdir / "second.json").write_text(json.dumps({"name": "second", "index": "i", "body": {}}))
    _patch(
        monkeypatch,
        {
            ("source", "first"): (200, _hits(1, ["a"])),
            ("dest", "first"): (200, _hits(1, ["a"])),
            ("source", "second"): (200, _hits(1, ["b"])),
            ("dest", "second"): (200, _hits(1, ["b"])),
        },
    )
    rc = shadow_diff.main(
        [
            "--source-host",
            "https://src.example",
            "--source-user",
            "u",
            "--source-password",
            "p",
            "--dest-host",
            "https://dst.example",
            "--dest-api-key",
            "id:secret",
            "--queries-dir",
            str(qdir),
            "--strict-exit-codes",
            "--log-format",
            "json",
            "--workers",
            "1",
        ]
    )
    assert rc == shadow_diff.EXIT_OK


def test_full_report_written(monkeypatch, tmp_path) -> None:
    queries = [{"name": "q1", "index": "logs", "body": {}}]
    _patch(
        monkeypatch,
        {
            ("source", "q1"): (200, _hits(1, ["a"])),
            ("dest", "q1"): (200, _hits(1, ["a"])),
        },
    )
    report_path = tmp_path / "out.json"
    rc = shadow_diff.main(_common_args(tmp_path, queries) + ["--report", str(report_path)])
    assert rc == shadow_diff.EXIT_OK
    report = json.loads(report_path.read_text())
    assert report["summary"]["queries_total"] == 1
    assert len(report["queries"]) == 1
    assert report["queries"][0]["passed"] is True


def test_missing_queries_returns_config_error(tmp_path) -> None:
    rc = shadow_diff.main(
        [
            "--source-host",
            "https://x",
            "--dest-host",
            "https://y",
            "--dest-api-key",
            "id:secret",
            "--queries-file",
            str(tmp_path / "missing.json"),
            "--strict-exit-codes",
        ]
    )
    assert rc == shadow_diff.EXIT_CONFIG
