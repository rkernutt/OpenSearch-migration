"""End-to-end tests for `s3_migration.s3_bulk_load`.

Uses ``moto`` for S3 (so no real network) and monkeypatches the bulk POST
function so no real Elasticsearch is required.
"""

from __future__ import annotations

import gzip
import io
import json
from typing import Any, Dict, List

import pytest

moto = pytest.importorskip("moto", reason="moto[s3] is required for bulk-load tests")

import boto3  # noqa: E402  (after pytest.importorskip)

from s3_migration import s3_bulk_load  # noqa: E402
from s3_migration.s3_common import (  # noqa: E402
    IndexEntry,
    Manifest,
    PartEntry,
    S3Uri,
    save_manifest,
    utc_now_iso,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


BUCKET = "test-bucket"
JOB_PREFIX = "jobs/2026-04-29/"
PART_KEY = "jobs/2026-04-29/data/logs-2024/part-00000.ndjson.gz"


def _gz(data: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(data)
    return buf.getvalue()


def _bulk_payload(n: int) -> bytes:
    out = io.BytesIO()
    for i in range(n):
        action = {"index": {"_index": "logs-2024", "_id": f"id-{i}"}}
        source = {"msg": f"hello-{i}", "i": i}
        out.write(json.dumps(action).encode())
        out.write(b"\n")
        out.write(json.dumps(source).encode())
        out.write(b"\n")
    return out.getvalue()


def _source_only_payload(n: int) -> bytes:
    out = io.BytesIO()
    for i in range(n):
        out.write(json.dumps({"msg": f"hi-{i}", "i": i}).encode())
        out.write(b"\n")
    return out.getvalue()


class _FakeBulkResponse:
    """Just enough of a `requests.Response` to satisfy `_process_bulk_response`."""

    def __init__(
        self,
        status_code: int = 200,
        items: List[Dict[str, Any]] | None = None,
        errors: bool = False,
    ) -> None:
        self.status_code = status_code
        self._payload = {"took": 1, "errors": errors, "items": items or []}
        self.text = json.dumps(self._payload)

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code} error")


def _ok_items(n: int) -> List[Dict[str, Any]]:
    return [{"index": {"status": 201, "_id": f"id-{i}"}} for i in range(n)]


def _mixed_items(n: int, fail_indices: List[int]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for i in range(n):
        if i in fail_indices:
            items.append(
                {
                    "index": {
                        "status": 400,
                        "error": {"type": "mapper_parsing_exception", "reason": "boom"},
                    }
                }
            )
        else:
            items.append({"index": {"status": 201, "_id": f"id-{i}"}})
    return items


@pytest.fixture
def s3_env(monkeypatch):
    """Spin up a moto-mocked S3 with a bucket and one bulk-format part."""
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_object(
            Bucket=BUCKET,
            Key=PART_KEY,
            Body=_gz(_bulk_payload(3)),
            ContentType="application/gzip",
        )
        # Default: no manifest. Tests that want one will write it themselves.
        yield {"client": client}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dry_run_loads_three_docs(s3_env, monkeypatch, capsys) -> None:
    posts: List[bytes] = []

    def _no_post(*a, **kw):
        posts.append(a[2] if len(a) > 2 else kw.get("body"))
        raise AssertionError("dry-run should not POST")

    monkeypatch.setattr(s3_bulk_load, "_post_bulk", _no_post)

    rc = s3_bulk_load.main(
        [
            "--s3-uri",
            f"s3://{BUCKET}/{JOB_PREFIX}",
            "--dry-run",
            "--strict-exit-codes",
            "--log-format",
            "json",
            "--max-in-flight",
            "1",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    summary = json.loads(out)
    assert summary["documents_succeeded"] == 3
    assert summary["documents_failed"] == 0
    assert summary["parts_total"] == 1
    assert summary["dry_run"] is True
    assert posts == []


def test_full_load_with_manifest(s3_env, monkeypatch, capsys) -> None:
    client = s3_env["client"]
    manifest = Manifest(
        job_id="job-001",
        created_at=utc_now_iso(),
        indices=[
            IndexEntry(
                name="logs-2024",
                doc_count_source=3,
                parts=[
                    PartEntry(
                        key="data/logs-2024/part-00000.ndjson.gz",
                        size_bytes=999,
                        doc_count=3,
                    )
                ],
            )
        ],
        source={"host": "https://example", "auth": "sigv4"},
    )
    save_manifest(client, S3Uri(bucket=BUCKET, key=JOB_PREFIX), manifest)

    captured: List[bytes] = []

    def _fake_post(host: str, dest_auth, body: bytes, timeout: int):
        captured.append(body)
        return _FakeBulkResponse(items=_ok_items(3))

    monkeypatch.setattr(s3_bulk_load, "_post_bulk", _fake_post)

    rc = s3_bulk_load.main(
        [
            "--s3-uri",
            f"s3://{BUCKET}/{JOB_PREFIX}",
            "--dest-host",
            "https://example.found.io",
            "--dest-api-key",
            "id:secret",
            "--strict-exit-codes",
            "--log-format",
            "json",
            "--max-in-flight",
            "1",
        ]
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["documents_succeeded"] == 3
    assert summary["documents_failed"] == 0
    assert summary["parts_completed"] == 1
    assert summary["dlq_used"] is False
    assert len(captured) == 1
    # _bulk body should be alternating action+source lines, 6 newlines.
    assert captured[0].count(b"\n") == 6


def test_per_doc_failures_go_to_dlq(s3_env, monkeypatch, capsys) -> None:
    def _fake_post(host: str, dest_auth, body: bytes, timeout: int):
        return _FakeBulkResponse(items=_mixed_items(3, fail_indices=[1]), errors=True)

    monkeypatch.setattr(s3_bulk_load, "_post_bulk", _fake_post)

    rc = s3_bulk_load.main(
        [
            "--s3-uri",
            f"s3://{BUCKET}/{JOB_PREFIX}",
            "--dest-host",
            "https://example.found.io",
            "--dest-api-key",
            "id:secret",
            "--strict-exit-codes",
            "--log-format",
            "json",
            "--max-in-flight",
            "1",
        ]
    )
    assert rc == s3_bulk_load.EXIT_DOC_ERRORS
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["documents_succeeded"] == 2
    assert summary["documents_failed"] == 1
    assert summary["dlq_used"] is True

    # DLQ object should now exist under jobs/.../dlq/
    client = s3_env["client"]
    listed = client.list_objects_v2(Bucket=BUCKET, Prefix=JOB_PREFIX + "dlq/")
    keys = [obj["Key"] for obj in listed.get("Contents", [])]
    assert any(k.endswith("__failed.ndjson.gz") for k in keys), keys

    # And it should contain one error record.
    dlq_obj = client.get_object(Bucket=BUCKET, Key=keys[0])
    raw = gzip.GzipFile(fileobj=io.BytesIO(dlq_obj["Body"].read())).read()
    lines = [json.loads(line) for line in raw.splitlines() if line]
    assert len(lines) == 1
    assert lines[0]["status"] == 400
    assert "boom" in lines[0]["error"]["reason"]


def test_no_dlq_aborts_on_doc_failure(s3_env, monkeypatch) -> None:
    def _fake_post(host: str, dest_auth, body: bytes, timeout: int):
        return _FakeBulkResponse(items=_mixed_items(3, fail_indices=[0]), errors=True)

    monkeypatch.setattr(s3_bulk_load, "_post_bulk", _fake_post)

    rc = s3_bulk_load.main(
        [
            "--s3-uri",
            f"s3://{BUCKET}/{JOB_PREFIX}",
            "--dest-host",
            "https://example.found.io",
            "--dest-api-key",
            "id:secret",
            "--no-dlq",
            "--strict-exit-codes",
            "--max-in-flight",
            "1",
        ]
    )
    # No DLQ: doc-level failure still surfaces as EXIT_DOC_ERRORS in strict
    # mode (the loader aborted the part, but the cause was data, not transport).
    assert rc == s3_bulk_load.EXIT_DOC_ERRORS


def test_source_only_ndjson_with_target_index(monkeypatch, capsys) -> None:
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_object(
            Bucket=BUCKET,
            Key=PART_KEY,
            Body=_gz(_source_only_payload(2)),
            ContentType="application/gzip",
        )

        captured: List[bytes] = []

        def _fake_post(host: str, dest_auth, body: bytes, timeout: int):
            captured.append(body)
            return _FakeBulkResponse(items=_ok_items(2))

        monkeypatch.setattr(s3_bulk_load, "_post_bulk", _fake_post)

        rc = s3_bulk_load.main(
            [
                "--s3-uri",
                f"s3://{BUCKET}/{JOB_PREFIX}",
                "--dest-host",
                "https://example.found.io",
                "--dest-api-key",
                "id:secret",
                "--target-index",
                "partner-drop",
                "--log-format",
                "json",
                "--strict-exit-codes",
                "--max-in-flight",
                "1",
            ]
        )
        assert rc == 0
        summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert summary["documents_succeeded"] == 2
        # Synthesised action lines should reference partner-drop.
        assert b'"_index":"partner-drop"' in captured[0]


def test_invalid_s3_uri_returns_config_error(monkeypatch) -> None:
    rc = s3_bulk_load.main(
        [
            "--s3-uri",
            "https://not-s3/",
            "--dest-host",
            "https://example.found.io",
            "--dest-api-key",
            "id:secret",
            "--strict-exit-codes",
        ]
    )
    assert rc == s3_bulk_load.EXIT_CONFIG


def test_missing_auth_returns_config_error(s3_env, monkeypatch) -> None:
    rc = s3_bulk_load.main(
        [
            "--s3-uri",
            f"s3://{BUCKET}/{JOB_PREFIX}",
            "--dest-host",
            "https://example.found.io",
            "--strict-exit-codes",
        ]
    )
    assert rc == s3_bulk_load.EXIT_CONFIG


def test_checkpoint_resume(s3_env, monkeypatch, tmp_path, capsys) -> None:
    """A checkpoint listing the only part as done should short-circuit."""
    ckpt = tmp_path / "ckpt.json"
    ckpt.write_text(json.dumps({"completed_parts": [PART_KEY]}))

    def _should_not_be_called(*a, **kw):
        raise AssertionError("part already completed; loader must not POST")

    monkeypatch.setattr(s3_bulk_load, "_post_bulk", _should_not_be_called)

    rc = s3_bulk_load.main(
        [
            "--s3-uri",
            f"s3://{BUCKET}/{JOB_PREFIX}",
            "--dest-host",
            "https://example.found.io",
            "--dest-api-key",
            "id:secret",
            "--checkpoint-file",
            str(ckpt),
            "--log-format",
            "json",
            "--strict-exit-codes",
            "--max-in-flight",
            "1",
        ]
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["parts_completed"] == 1
    assert summary["documents_succeeded"] == 0  # nothing posted this run
