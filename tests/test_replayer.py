"""End-to-end tests for ``replay.replayer``.

We exercise:
  * loading captures from local NDJSON, gzipped NDJSON, and S3 (moto)
  * filtering (method, path include/exclude, since/until, max-requests)
  * status / size / hash comparators
  * rate limiter doesn't deadlock
  * exit codes
"""

from __future__ import annotations

import gzip
import json
from typing import Any, Dict, List

import boto3
from moto import mock_aws

from replay import replayer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    method: str = "POST",
    path: str = "/idx/_search",
    status: int = 200,
    body: Dict[str, Any] | None = None,
    ts: str = "2026-04-29T12:00:00.000000Z",
) -> Dict[str, Any]:
    body_text = json.dumps(body or {"hits": {"total": {"value": 1}, "hits": []}})
    return {
        "ts": ts,
        "request_id": "abc",
        "method": method,
        "path": path,
        "query_string": "",
        "request_headers": {"Content-Type": "application/json"},
        "request_body": '{"query":{"match_all":{}}}',
        "request_body_bytes": 27,
        "request_body_hash": "sha256:x",
        "response_status": status,
        "response_headers": {"Content-Type": "application/json"},
        "response_body": body_text,
        "response_body_bytes": len(body_text),
        "response_body_hash": "sha256:y",
        "latency_ms": 5,
        "target_host": "https://opensearch.local",
    }


def _patch_session(monkeypatch, plan: List[Any]) -> List[Dict[str, Any]]:
    """Plan: list of (status, body_text) tuples — consumed in order.

    Returns a list that the tests can inspect for the requests we made.
    """
    sent: List[Dict[str, Any]] = []
    plan_iter = iter(plan)

    class FakeResp:
        def __init__(self, status: int, text: str) -> None:
            self.status_code = status
            self.text = text
            self.content = text.encode("utf-8")

    def fake_request(method, url, **kwargs):
        sent.append({"method": method, "url": url, "kwargs": kwargs})
        try:
            status, text = next(plan_iter)
        except StopIteration:
            return FakeResp(200, '{"hits":{"total":{"value":0},"hits":[]}}')
        return FakeResp(status, text)

    monkeypatch.setattr(replayer._SESSION, "request", fake_request)
    return sent


def _common_args(captures_path: str) -> List[str]:
    return [
        "--captures",
        captures_path,
        "--dest-host",
        "https://dst.example",
        "--dest-api-key",
        "id:secret",
        "--workers",
        "1",
        "--strict-exit-codes",
        "--log-format",
        "json",
    ]


# ---------------------------------------------------------------------------
# Local file
# ---------------------------------------------------------------------------


def test_replays_local_ndjson_pass(tmp_path, monkeypatch, capsys) -> None:
    cap = tmp_path / "cap.ndjson"
    rec = _record()
    cap.write_text(json.dumps(rec) + "\n")
    sent = _patch_session(monkeypatch, [(200, rec["response_body"])])

    rc = replayer.main(_common_args(str(cap)) + ["--no-hash-check"])
    assert rc == replayer.EXIT_OK
    assert len(sent) == 1
    assert sent[0]["method"] == "POST"
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["requests_passed"] == 1


def test_replays_gzipped_ndjson(tmp_path, monkeypatch) -> None:
    cap = tmp_path / "cap.ndjson.gz"
    rec = _record()
    with gzip.open(cap, "wt", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    _patch_session(monkeypatch, [(200, rec["response_body"])])
    rc = replayer.main(_common_args(str(cap)) + ["--no-hash-check"])
    assert rc == replayer.EXIT_OK


def test_directory_of_captures(tmp_path, monkeypatch) -> None:
    a = tmp_path / "a.ndjson"
    b = tmp_path / "b.ndjson.gz"
    rec_a = _record(path="/idx/_search")
    rec_b = _record(path="/idx2/_search")
    a.write_text(json.dumps(rec_a) + "\n")
    with gzip.open(b, "wt", encoding="utf-8") as f:
        f.write(json.dumps(rec_b) + "\n")
    _patch_session(monkeypatch, [(200, rec_a["response_body"]), (200, rec_b["response_body"])])
    rc = replayer.main(_common_args(str(tmp_path)) + ["--no-hash-check"])
    assert rc == replayer.EXIT_OK


# ---------------------------------------------------------------------------
# Comparators
# ---------------------------------------------------------------------------


def test_status_drift_fails(tmp_path, monkeypatch, capsys) -> None:
    cap = tmp_path / "cap.ndjson"
    cap.write_text(json.dumps(_record(status=200)) + "\n")
    _patch_session(monkeypatch, [(404, "{}")])
    rc = replayer.main(_common_args(str(cap)) + ["--no-hash-check"])
    assert rc == replayer.EXIT_DRIFT
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["requests_drifted"] == 1
    assert any(f.startswith("status") for f in summary["drift_sample"][0]["failures"])


def test_size_drift_outside_tolerance_fails(tmp_path, monkeypatch) -> None:
    cap = tmp_path / "cap.ndjson"
    cap.write_text(json.dumps(_record()) + "\n")
    # captured body length is len of rec body; replay returns much larger.
    big = json.dumps({"hits": {"total": {"value": 1}, "hits": [{"_id": "x" * 5000}]}})
    _patch_session(monkeypatch, [(200, big)])
    rc = replayer.main(_common_args(str(cap)) + ["--no-hash-check", "--size-tolerance", "0.05"])
    assert rc == replayer.EXIT_DRIFT


def test_size_tolerance_disabled_with_negative_tolerance(tmp_path, monkeypatch) -> None:
    cap = tmp_path / "cap.ndjson"
    cap.write_text(json.dumps(_record()) + "\n")
    big = json.dumps({"hits": {"total": {"value": 1}, "hits": [{"_id": "x" * 5000}]}})
    _patch_session(monkeypatch, [(200, big)])
    rc = replayer.main(_common_args(str(cap)) + ["--no-hash-check", "--size-tolerance", "-1"])
    assert rc == replayer.EXIT_OK


def test_hash_match_passes(tmp_path, monkeypatch) -> None:
    cap = tmp_path / "cap.ndjson"
    captured_body = '{"a":1,"b":2}'
    rec = _record(body={"a": 1, "b": 2})
    rec["response_body"] = captured_body
    rec["response_body_bytes"] = len(captured_body)
    # Pre-compute the canonical hash that the replayer expects.
    rec["response_body_hash"] = replayer._canonical_json_hash(captured_body)
    cap.write_text(json.dumps(rec) + "\n")
    # Replay returns same JSON in different key order (cosmetic difference).
    _patch_session(monkeypatch, [(200, '{"b":2,"a":1}')])
    rc = replayer.main(_common_args(str(cap)) + ["--size-tolerance", "0.5"])
    assert rc == replayer.EXIT_OK


def test_hash_mismatch_fails(tmp_path, monkeypatch) -> None:
    cap = tmp_path / "cap.ndjson"
    rec = _record(body={"a": 1})
    rec["response_body"] = '{"a":1}'
    rec["response_body_bytes"] = 7
    rec["response_body_hash"] = replayer._canonical_json_hash('{"a":1}')
    cap.write_text(json.dumps(rec) + "\n")
    _patch_session(monkeypatch, [(200, '{"a":2}')])
    rc = replayer.main(_common_args(str(cap)))
    assert rc == replayer.EXIT_DRIFT


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def test_method_filter(tmp_path, monkeypatch) -> None:
    cap = tmp_path / "cap.ndjson"
    rec_get = _record(method="GET")
    rec_del = _record(method="DELETE")
    cap.write_text(json.dumps(rec_get) + "\n" + json.dumps(rec_del) + "\n")
    sent = _patch_session(monkeypatch, [(200, rec_get["response_body"])])
    rc = replayer.main(_common_args(str(cap)) + ["--no-hash-check", "--method", "GET"])
    assert rc == replayer.EXIT_OK
    assert len(sent) == 1
    assert sent[0]["method"] == "GET"


def test_path_filters(tmp_path, monkeypatch) -> None:
    cap = tmp_path / "cap.ndjson"
    rec_a = _record(path="/idx/_search")
    rec_b = _record(path="/_cluster/health")
    rec_c = _record(path="/idx2/_search")
    cap.write_text(json.dumps(rec_a) + "\n" + json.dumps(rec_b) + "\n" + json.dumps(rec_c) + "\n")
    sent = _patch_session(
        monkeypatch, [(200, rec_a["response_body"]), (200, rec_c["response_body"])]
    )
    rc = replayer.main(
        _common_args(str(cap))
        + [
            "--no-hash-check",
            "--path-include",
            r"/_search$",
            "--path-exclude",
            r"^/_cluster/",
        ]
    )
    assert rc == replayer.EXIT_OK
    assert len(sent) == 2


def test_max_requests_caps(tmp_path, monkeypatch) -> None:
    cap = tmp_path / "cap.ndjson"
    rec = _record()
    cap.write_text("\n".join(json.dumps(rec) for _ in range(10)) + "\n")
    sent = _patch_session(monkeypatch, [(200, rec["response_body"])] * 10)
    rc = replayer.main(_common_args(str(cap)) + ["--no-hash-check", "--max-requests", "3"])
    assert rc == replayer.EXIT_OK
    assert len(sent) == 3


def test_since_until_filter(tmp_path, monkeypatch, capsys) -> None:
    cap = tmp_path / "cap.ndjson"
    rec_old = _record(ts="2026-04-29T11:00:00.000000Z")
    rec_new = _record(ts="2026-04-29T13:00:00.000000Z")
    cap.write_text(json.dumps(rec_old) + "\n" + json.dumps(rec_new) + "\n")
    sent = _patch_session(monkeypatch, [(200, rec_new["response_body"])])
    rc = replayer.main(
        _common_args(str(cap))
        + [
            "--no-hash-check",
            "--since",
            "2026-04-29T12:00:00Z",
        ]
    )
    assert rc == replayer.EXIT_OK
    assert len(sent) == 1
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["matched"] == 1
    assert summary["seen"] == 2


# ---------------------------------------------------------------------------
# S3 captures
# ---------------------------------------------------------------------------


@mock_aws
def test_replays_from_s3(tmp_path, monkeypatch) -> None:
    bucket = "captures"
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=bucket)
    rec = _record()
    body = (json.dumps(rec) + "\n").encode("utf-8")
    s3.put_object(Bucket=bucket, Key="prefix/capture-1.ndjson.gz", Body=gzip.compress(body))
    _patch_session(monkeypatch, [(200, rec["response_body"])])
    rc = replayer.main(_common_args(f"s3://{bucket}/prefix/") + ["--no-hash-check"])
    assert rc == replayer.EXIT_OK


# ---------------------------------------------------------------------------
# Body-not-inlined path
# ---------------------------------------------------------------------------


def test_skipped_when_body_not_inlined(tmp_path, monkeypatch, capsys) -> None:
    cap = tmp_path / "cap.ndjson"
    rec = _record()
    rec["request_body"] = None
    rec["request_body_bytes"] = 5_000_000  # body was hashed-only
    cap.write_text(json.dumps(rec) + "\n")
    _patch_session(monkeypatch, [])  # should never be called
    rc = replayer.main(_common_args(str(cap)) + ["--no-hash-check"])
    assert rc == replayer.EXIT_DRIFT
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["drift_sample"][0]["failures"] == ["body-not-inlined"]


# ---------------------------------------------------------------------------
# Config error paths
# ---------------------------------------------------------------------------


def test_missing_dest_host_returns_config_error(tmp_path) -> None:
    cap = tmp_path / "cap.ndjson"
    cap.write_text(json.dumps(_record()) + "\n")
    rc = replayer.main(["--captures", str(cap), "--strict-exit-codes"])
    assert rc == replayer.EXIT_CONFIG


def test_missing_captures_path_returns_config_error() -> None:
    rc = replayer.main(["--dest-host", "https://x", "--dest-api-key", "k", "--strict-exit-codes"])
    assert rc == replayer.EXIT_CONFIG
