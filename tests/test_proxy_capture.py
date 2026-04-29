"""Tests for ``Proxy.capture``.

Local-mode end-to-end (write-and-rotate), S3-mode via ``moto``, plus the
filtering / redaction logic.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from Proxy.capture import (
    CaptureConfig,
    Capturer,
    CaptureRecord,
    _redact_headers,
    make_record,
)

# ---------------------------------------------------------------------------
# CaptureConfig
# ---------------------------------------------------------------------------


def test_config_defaults_to_off() -> None:
    cfg = CaptureConfig.from_env({})
    assert cfg.mode == "off"


def test_config_local_from_env() -> None:
    cfg = CaptureConfig.from_env(
        {
            "PROXY_CAPTURE_MODE": "local",
            "PROXY_CAPTURE_DIR": "/tmp/cap",
            "PROXY_CAPTURE_PATH_INCLUDE": ".*/_search,.*/_msearch",
            "PROXY_CAPTURE_PATH_EXCLUDE": "_cluster/.*",
            "PROXY_CAPTURE_METHODS": "GET,POST",
            "PROXY_CAPTURE_INCLUDE_BODIES": "false",
            "PROXY_CAPTURE_MAX_BODY_BYTES": "1024",
        }
    )
    assert cfg.mode == "local"
    assert cfg.local_dir == Path("/tmp/cap")
    assert cfg.path_include == [".*/_search", ".*/_msearch"]
    assert cfg.path_exclude == ["_cluster/.*"]
    assert cfg.methods == ["GET", "POST"]
    assert cfg.include_bodies is False
    assert cfg.max_body_bytes == 1024


def test_config_invalid_mode_rejected() -> None:
    with pytest.raises(ValueError):
        CaptureConfig.from_env({"PROXY_CAPTURE_MODE": "weird"})


# ---------------------------------------------------------------------------
# make_record + redaction
# ---------------------------------------------------------------------------


def test_redact_headers_strips_authorization_and_cookie() -> None:
    redacted = _redact_headers(
        {
            "Authorization": "ApiKey abc",
            "Cookie": "sid=1",
            "X-Api-Key": "xxx",
            "Content-Type": "application/json",
        }
    )
    assert "Authorization" not in redacted
    assert "Cookie" not in redacted
    assert "X-Api-Key" not in redacted
    assert redacted["Content-Type"] == "application/json"


def test_make_record_inlines_small_body_and_hashes_large() -> None:
    small_body = b'{"q":"x"}'
    big_body = b"x" * 4096
    rec = make_record(
        method="POST",
        path="/idx/_search",
        query_string="",
        request_headers={"Content-Type": "application/json", "Authorization": "Basic xx"},
        request_body=small_body,
        response_status=200,
        response_headers={"Content-Type": "application/json"},
        response_body=big_body,
        latency_ms=42,
        target_host="https://opensearch.local",
        include_bodies=True,
        max_body_bytes=1024,
    )
    assert rec.request_body == '{"q":"x"}'
    assert rec.request_body_bytes == len(small_body)
    assert rec.request_body_hash.startswith("sha256:")
    assert rec.response_body is None  # exceeded max_body_bytes
    assert rec.response_body_bytes == 4096
    assert rec.response_body_hash.startswith("sha256:")
    assert "Authorization" not in rec.request_headers


def test_make_record_with_include_bodies_false_drops_inline() -> None:
    rec = make_record(
        method="GET",
        path="/idx/_search",
        query_string="",
        request_headers={},
        request_body=b'{"q":"x"}',
        response_status=200,
        response_headers={},
        response_body=b'{"hits":{}}',
        latency_ms=1,
        target_host="https://x",
        include_bodies=False,
        max_body_bytes=1_000_000,
    )
    assert rec.request_body is None
    assert rec.response_body is None
    # Hashes still recorded.
    assert rec.request_body_hash.startswith("sha256:")
    assert rec.response_body_hash.startswith("sha256:")


# ---------------------------------------------------------------------------
# Local capturer
# ---------------------------------------------------------------------------


def _make_rec(path: str = "/idx/_search", method: str = "POST") -> CaptureRecord:
    return make_record(
        method=method,
        path=path,
        query_string="",
        request_headers={},
        request_body=b'{"q":"x"}',
        response_status=200,
        response_headers={},
        response_body=b'{"hits":{}}',
        latency_ms=1,
        target_host="https://x",
        include_bodies=True,
        max_body_bytes=1_000_000,
    )


def _drain(capturer: Capturer, expect: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while capturer.written + capturer.dropped < expect and time.monotonic() < deadline:
        time.sleep(0.05)


def test_local_capturer_writes_ndjson(tmp_path) -> None:
    cfg = CaptureConfig(mode="local", local_dir=tmp_path)
    cap = Capturer(cfg)
    cap.start()
    try:
        for i in range(5):
            cap.record(_make_rec(path=f"/idx/_search?q={i}"))
        _drain(cap, expect=5)
    finally:
        cap.stop(timeout=2.0)

    files = sorted(tmp_path.glob("capture-*.ndjson"))
    assert files, "no capture file produced"
    lines: list[dict] = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                lines.append(json.loads(line))
    assert len(lines) == 5
    for ln in lines:
        assert ln["method"] == "POST"
        assert ln["request_body"] == '{"q":"x"}'
        assert ln["response_status"] == 200


def test_method_filter_drops_unwanted(tmp_path) -> None:
    cfg = CaptureConfig(mode="local", local_dir=tmp_path, methods=["GET"])
    cap = Capturer(cfg)
    cap.start()
    try:
        cap.record(_make_rec(method="GET"))
        cap.record(_make_rec(method="POST"))
        cap.record(_make_rec(method="DELETE"))
        _drain(cap, expect=1)
    finally:
        cap.stop(timeout=2.0)

    lines = []
    for f in tmp_path.glob("capture-*.ndjson"):
        lines.extend(f.read_text().splitlines())
    decoded = [json.loads(line) for line in lines if line.strip()]
    assert len(decoded) == 1
    assert decoded[0]["method"] == "GET"


def test_path_include_and_exclude_filter(tmp_path) -> None:
    cfg = CaptureConfig(
        mode="local",
        local_dir=tmp_path,
        path_include=[r".*/_search$"],
        path_exclude=[r"^/_cluster/"],
    )
    cap = Capturer(cfg)
    cap.start()
    try:
        cap.record(_make_rec(path="/idx/_search"))
        cap.record(_make_rec(path="/idx/_msearch"))
        cap.record(_make_rec(path="/_cluster/health/_search"))  # excluded
        _drain(cap, expect=1)
    finally:
        cap.stop(timeout=2.0)

    decoded: list[dict] = []
    for f in tmp_path.glob("capture-*.ndjson"):
        for line in f.read_text().splitlines():
            if line.strip():
                decoded.append(json.loads(line))
    paths = sorted(d["path"] for d in decoded)
    assert paths == ["/idx/_search"]


def test_local_capturer_off_is_noop(tmp_path) -> None:
    cfg = CaptureConfig(mode="off")
    cap = Capturer(cfg)
    cap.start()
    cap.record(_make_rec())
    cap.stop(timeout=1.0)
    assert cap.written == 0
    assert cap.dropped == 0


# ---------------------------------------------------------------------------
# S3 capturer
# ---------------------------------------------------------------------------


@mock_aws
def test_s3_capturer_uploads_gzipped_ndjson() -> None:
    bucket = "test-cap"
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=bucket)

    cfg = CaptureConfig(
        mode="s3",
        s3_uri=f"s3://{bucket}/captures/",
        s3_region="us-east-1",
        rotate_seconds=1,  # force quick flush
    )
    cap = Capturer(cfg)
    cap.start()
    try:
        for _ in range(3):
            cap.record(_make_rec())
        # Wait for the timer-triggered flush.
        deadline = time.monotonic() + 5.0
        while cap.written < 3 and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        cap.stop(timeout=3.0)

    s3 = boto3.client("s3", region_name="us-east-1")
    keys = [obj["Key"] for obj in s3.list_objects_v2(Bucket=bucket).get("Contents", [])]
    assert keys, "no captures uploaded"
    body = s3.get_object(Bucket=bucket, Key=keys[0])["Body"].read()
    decoded = gzip.decompress(body).decode("utf-8")
    rows = [json.loads(line) for line in decoded.splitlines() if line.strip()]
    assert len(rows) >= 1
    assert rows[0]["method"] == "POST"


# ---------------------------------------------------------------------------
# Backpressure / queue full
# ---------------------------------------------------------------------------


def test_full_queue_drops_oldest_and_counts(tmp_path) -> None:
    """A small queue size with the worker not yet running: every put should
    fill up and the dropped counter must increment.
    """
    cfg = CaptureConfig(mode="local", local_dir=tmp_path, queue_capacity=2)
    cap = Capturer(cfg)
    # Don't start the worker; queue stays full.
    for _ in range(10):
        cap.record(_make_rec())
    assert cap.dropped >= 8
