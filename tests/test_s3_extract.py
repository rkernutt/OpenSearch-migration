"""End-to-end tests for `s3_migration.s3_extract`.

Uses ``moto`` to fake S3 and monkeypatches the OpenSearch HTTP helpers so no
real cluster is required. Includes an extract → load round-trip that proves
the on-disk format is exactly what the loader expects.
"""

from __future__ import annotations

import gzip
import io
import json
from typing import Any, Dict, List

import pytest

moto = pytest.importorskip("moto", reason="moto[s3] is required")

import boto3  # noqa: E402

from s3_migration import s3_bulk_load, s3_extract  # noqa: E402
from s3_migration.s3_common import S3Uri, load_manifest  # noqa: E402

BUCKET = "extract-bucket"
JOB_PREFIX = "jobs/2026-04-29/"


# ---------------------------------------------------------------------------
# Fake OpenSearch helpers
# ---------------------------------------------------------------------------


class _FakeSource:
    """Generates deterministic hits per (index, slice_id) and serves scroll pages."""

    def __init__(
        self,
        docs_per_slice: Dict[str, int],
        page_size: int = 1000,
    ) -> None:
        self.docs_per_slice = docs_per_slice
        self.page_size = page_size
        self._scrolls: Dict[str, Dict[str, Any]] = {}
        self._next_scroll_id = 0

    def _key(self, index: str, slice_id: int) -> str:
        return f"{index}::{slice_id}"

    def _hits_for(self, index: str, slice_id: int) -> List[Dict[str, Any]]:
        n = self.docs_per_slice.get(self._key(index, slice_id), 0)
        return [
            {
                "_id": f"{index}-{slice_id:02d}-{i:05d}",
                "_index": index,
                "_source": {"index": index, "slice": slice_id, "i": i, "msg": f"doc-{i}"},
            }
            for i in range(n)
        ]

    def post_search(
        self,
        host: str,
        index: str,
        body: Dict[str, Any],
        auth,
        scroll: str,
    ) -> Dict[str, Any]:
        slice_id = (body.get("slice") or {}).get("id", 0)
        all_hits = self._hits_for(index, slice_id)
        page, rest = all_hits[: self.page_size], all_hits[self.page_size :]
        sid = f"sid-{self._next_scroll_id}"
        self._next_scroll_id += 1
        self._scrolls[sid] = {"index": index, "slice_id": slice_id, "rest": rest}
        return {
            "_scroll_id": sid if rest else "",
            "hits": {"hits": page, "total": {"value": len(all_hits)}},
        }

    def post_scroll(self, host: str, scroll_id: str, auth, scroll: str) -> Dict[str, Any]:
        ctx = self._scrolls.get(scroll_id)
        if not ctx:
            return {"_scroll_id": "", "hits": {"hits": []}}
        page, rest = ctx["rest"][: self.page_size], ctx["rest"][self.page_size :]
        new_sid = f"sid-{self._next_scroll_id}"
        self._next_scroll_id += 1
        if rest:
            self._scrolls[new_sid] = {**ctx, "rest": rest}
            return {"_scroll_id": new_sid, "hits": {"hits": page}}
        return {"_scroll_id": "", "hits": {"hits": page}}

    def delete_scroll(self, host: str, scroll_id: str, auth) -> None:
        self._scrolls.pop(scroll_id, None)

    def get_count(self, host: str, index: str, body: Dict[str, Any], auth) -> int:
        return sum(v for k, v in self.docs_per_slice.items() if k.startswith(f"{index}::"))


@pytest.fixture
def s3_env():
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield {"client": client}


def _patch_source(monkeypatch, source: _FakeSource) -> None:
    monkeypatch.setattr(s3_extract, "_post_search", source.post_search)
    monkeypatch.setattr(s3_extract, "_post_scroll", source.post_scroll)
    monkeypatch.setattr(s3_extract, "_delete_scroll", source.delete_scroll)
    monkeypatch.setattr(s3_extract, "_get_count", source.get_count)


def _read_part(client, bucket: str, key: str) -> List[bytes]:
    obj = client.get_object(Bucket=bucket, Key=key)
    raw = gzip.GzipFile(fileobj=io.BytesIO(obj["Body"].read())).read()
    return [line for line in raw.split(b"\n") if line]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_extract_single_slice_basic(s3_env, monkeypatch, capsys) -> None:
    source = _FakeSource(docs_per_slice={"logs::0": 5}, page_size=1000)
    _patch_source(monkeypatch, source)

    rc = s3_extract.main(
        [
            "--source-host",
            "https://example",
            "--source-user",
            "u",
            "--source-password",
            "p",
            "--indices",
            "logs",
            "--s3-uri",
            f"s3://{BUCKET}/{JOB_PREFIX}",
            "--slices",
            "1",
            "--page-size",
            "1000",
            "--part-size-mb",
            "64",
            "--strict-exit-codes",
            "--log-format",
            "json",
        ]
    )
    assert rc == 0, capsys.readouterr().err
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["documents_extracted"] == 5
    assert summary["parts_total"] == 1

    client = s3_env["client"]
    manifest = load_manifest(client, S3Uri(bucket=BUCKET, key=JOB_PREFIX))
    assert manifest is not None
    assert manifest.indices[0].name == "logs"
    assert manifest.indices[0].doc_count_source == 5
    assert sum(p.doc_count for p in manifest.indices[0].parts) == 5

    # Verify part contents are bulk-format (alternating action, source).
    listing = client.list_objects_v2(Bucket=BUCKET, Prefix=f"{JOB_PREFIX}data/logs/")["Contents"]
    assert len(listing) == 1
    lines = _read_part(client, BUCKET, listing[0]["Key"])
    assert len(lines) == 10  # 5 docs × 2 lines each
    parsed = [json.loads(line) for line in lines]
    assert parsed[0] == {"index": {"_index": "logs", "_id": "logs-00-00000"}}
    assert parsed[1]["msg"] == "doc-0"


def test_extract_multiple_slices(s3_env, monkeypatch, capsys) -> None:
    source = _FakeSource(docs_per_slice={"logs::0": 3, "logs::1": 4, "logs::2": 0, "logs::3": 7})
    _patch_source(monkeypatch, source)

    rc = s3_extract.main(
        [
            "--source-host",
            "https://example",
            "--source-user",
            "u",
            "--source-password",
            "p",
            "--indices",
            "logs",
            "--s3-uri",
            f"s3://{BUCKET}/{JOB_PREFIX}",
            "--slices",
            "4",
            "--strict-exit-codes",
            "--log-format",
            "json",
        ]
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["documents_extracted"] == 14

    client = s3_env["client"]
    manifest = load_manifest(client, S3Uri(bucket=BUCKET, key=JOB_PREFIX))
    assert manifest is not None
    parts = manifest.indices[0].parts
    # Empty slice produces no part; the others produce one each (small data).
    assert len(parts) == 3
    slice_ids = sorted({int(p.key.split("slice-")[1].split("-")[0]) for p in parts})
    assert slice_ids == [0, 1, 3]


def test_extract_skips_completed_slices(s3_env, monkeypatch, tmp_path, capsys) -> None:
    source = _FakeSource(docs_per_slice={"logs::0": 3, "logs::1": 4})
    _patch_source(monkeypatch, source)
    ckpt = tmp_path / "ckpt.json"
    ckpt.write_text(json.dumps({"completed_slices": ["logs::0"]}))

    rc = s3_extract.main(
        [
            "--source-host",
            "https://example",
            "--source-user",
            "u",
            "--source-password",
            "p",
            "--indices",
            "logs",
            "--s3-uri",
            f"s3://{BUCKET}/{JOB_PREFIX}",
            "--slices",
            "2",
            "--checkpoint-file",
            str(ckpt),
            "--strict-exit-codes",
            "--log-format",
            "json",
        ]
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    # Only slice 1 ran this round → 4 docs.
    assert summary["documents_extracted"] == 4

    # Checkpoint now has both slices.
    saved = json.loads(ckpt.read_text())
    assert saved["completed_slices"] == ["logs::0", "logs::1"]


def test_extract_failed_index_returns_4(s3_env, monkeypatch, capsys) -> None:
    source = _FakeSource(docs_per_slice={"logs::0": 3})

    def boom(*a, **kw):  # noqa: ARG001
        import requests

        raise requests.ConnectionError("network down")

    monkeypatch.setattr(s3_extract, "_post_search", boom)
    monkeypatch.setattr(s3_extract, "_get_count", source.get_count)
    monkeypatch.setattr(s3_extract, "_delete_scroll", source.delete_scroll)
    monkeypatch.setattr(s3_extract, "_post_scroll", source.post_scroll)

    rc = s3_extract.main(
        [
            "--source-host",
            "https://example",
            "--source-user",
            "u",
            "--source-password",
            "p",
            "--indices",
            "logs",
            "--s3-uri",
            f"s3://{BUCKET}/{JOB_PREFIX}",
            "--slices",
            "1",
            "--strict-exit-codes",
            "--log-format",
            "json",
        ]
    )
    assert rc == s3_extract.EXIT_INDEX_FAILURES
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "logs" in summary["indices_failed"]


def test_dry_run_does_not_upload(s3_env, monkeypatch, capsys) -> None:
    source = _FakeSource(docs_per_slice={"logs::0": 3})
    _patch_source(monkeypatch, source)

    rc = s3_extract.main(
        [
            "--source-host",
            "https://example",
            "--source-user",
            "u",
            "--source-password",
            "p",
            "--indices",
            "logs",
            "--s3-uri",
            f"s3://{BUCKET}/{JOB_PREFIX}",
            "--dry-run",
            "--strict-exit-codes",
            "--log-format",
            "json",
        ]
    )
    assert rc == 0
    client = s3_env["client"]
    listing = client.list_objects_v2(Bucket=BUCKET, Prefix=JOB_PREFIX).get("Contents", [])
    # Final manifest is suppressed in dry-run; nothing in the bucket.
    assert listing == []


def test_via_proxy_label_in_manifest(s3_env, monkeypatch) -> None:
    source = _FakeSource(docs_per_slice={"logs::0": 1})
    _patch_source(monkeypatch, source)

    rc = s3_extract.main(
        [
            "--source-host",
            "http://proxy.local",
            "--source-user",
            "px",
            "--source-password",
            "pp",
            "--via-proxy",
            "--indices",
            "logs",
            "--s3-uri",
            f"s3://{BUCKET}/{JOB_PREFIX}",
            "--slices",
            "1",
            "--strict-exit-codes",
        ]
    )
    assert rc == 0
    client = s3_env["client"]
    manifest = load_manifest(client, S3Uri(bucket=BUCKET, key=JOB_PREFIX))
    assert manifest is not None
    assert manifest.source["auth"] == "proxy"


def test_time_window_query_added(monkeypatch) -> None:
    """Smoke test that --since/--until wraps the base query with a range filter."""
    seen: Dict[str, Any] = {}

    def fake_post_search(host, index, body, auth, scroll):  # noqa: ARG001
        seen["body"] = body
        return {"_scroll_id": "", "hits": {"hits": []}}

    monkeypatch.setattr(s3_extract, "_post_search", fake_post_search)
    monkeypatch.setattr(s3_extract, "_get_count", lambda *a, **k: 0)
    monkeypatch.setattr(s3_extract, "_delete_scroll", lambda *a, **k: None)
    monkeypatch.setattr(s3_extract, "_post_scroll", lambda *a, **k: {"hits": {"hits": []}})

    from moto import mock_aws

    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        rc = s3_extract.main(
            [
                "--source-host",
                "https://example",
                "--source-user",
                "u",
                "--source-password",
                "p",
                "--indices",
                "logs",
                "--s3-uri",
                f"s3://{BUCKET}/jobs/x/",
                "--slices",
                "1",
                "--time-field",
                "@timestamp",
                "--since",
                "2026-04-01",
                "--until",
                "2026-04-30",
                "--strict-exit-codes",
            ]
        )
        assert rc == 0
        q = seen["body"]["query"]
        assert q["bool"]["filter"][0]["range"]["@timestamp"] == {
            "gte": "2026-04-01",
            "lt": "2026-04-30",
        }


def test_extract_then_load_roundtrip(s3_env, monkeypatch, capsys) -> None:
    """Extract to S3, then point the loader at the same prefix and verify counts."""
    source = _FakeSource(docs_per_slice={"orders::0": 7, "orders::1": 5})
    _patch_source(monkeypatch, source)

    rc = s3_extract.main(
        [
            "--source-host",
            "https://example",
            "--source-user",
            "u",
            "--source-password",
            "p",
            "--indices",
            "orders",
            "--s3-uri",
            f"s3://{BUCKET}/{JOB_PREFIX}",
            "--slices",
            "2",
            "--strict-exit-codes",
            "--log-format",
            "json",
        ]
    )
    assert rc == 0
    capsys.readouterr()  # clear extract summary

    posted: List[bytes] = []

    def fake_post(host, dest_auth, body, timeout):  # noqa: ARG001
        posted.append(body)

        # Count action lines (every other line) → success items
        n = body.count(b"\n") // 2

        class R:
            status_code = 200
            text = ""

            def json(self_inner):
                return {
                    "errors": False,
                    "items": [{"index": {"status": 201, "_id": f"x-{i}"}} for i in range(n)],
                }

            def raise_for_status(self_inner):
                pass

        return R()

    monkeypatch.setattr(s3_bulk_load, "_post_bulk", fake_post)

    rc2 = s3_bulk_load.main(
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
    assert rc2 == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["documents_succeeded"] == 12
    assert summary["documents_failed"] == 0
    # Loader should have routed bulk bodies through the manifest's parts.
    assert posted, "loader posted no bulk requests"
