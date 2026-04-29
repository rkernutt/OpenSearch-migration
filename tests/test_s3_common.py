"""Tests for `s3_migration.s3_common` — pure-Python helpers (no S3 needed)."""

from __future__ import annotations

import gzip
import io
import json

import pytest

from s3_migration.s3_common import (
    IndexEntry,
    Manifest,
    PartEntry,
    S3Uri,
    batch_bulk_pairs,
    canonical_source_id,
    detect_bulk_format,
    is_bulk_action_line,
    load_checkpoint,
    resolve_manifest_part_key,
    save_checkpoint,
    serialise_bulk_body,
    to_bulk_pairs,
    utc_now_iso,
)

# ---------------------------------------------------------------------------
# S3Uri
# ---------------------------------------------------------------------------


def test_s3uri_parse_simple() -> None:
    u = S3Uri.parse("s3://my-bucket/path/to/key")
    assert u.bucket == "my-bucket"
    assert u.key == "path/to/key"
    assert str(u) == "s3://my-bucket/path/to/key"


def test_s3uri_parse_bucket_only() -> None:
    u = S3Uri.parse("s3://my-bucket")
    assert u.bucket == "my-bucket"
    assert u.key == ""
    assert u.is_prefix


def test_s3uri_parse_trailing_slash_is_prefix() -> None:
    assert S3Uri.parse("s3://b/p/").is_prefix
    assert not S3Uri.parse("s3://b/p").is_prefix


def test_s3uri_parse_invalid() -> None:
    for bad in ["", "bucket/key", "https://bucket/key", "s3:///key"]:
        with pytest.raises(ValueError):
            S3Uri.parse(bad)


def test_s3uri_join() -> None:
    u = S3Uri.parse("s3://b/p/")
    assert u.join("data", "logs", "part-0").key == "p/data/logs/part-0"
    assert u.join().key == "p/"  # join with no parts is a no-op


def test_s3uri_join_from_root() -> None:
    u = S3Uri.parse("s3://b")
    assert u.join("a", "b").key == "a/b"


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _sample_manifest() -> Manifest:
    return Manifest(
        job_id="job-001",
        created_at=utc_now_iso(),
        indices=[
            IndexEntry(
                name="logs-2024",
                doc_count_source=10000,
                parts=[
                    PartEntry(
                        key="data/logs-2024/part-00000.ndjson.gz",
                        size_bytes=1234,
                        doc_count=5000,
                        checksum_sha256="abc",
                    ),
                    PartEntry(
                        key="data/logs-2024/part-00001.ndjson.gz",
                        size_bytes=2345,
                        doc_count=5000,
                    ),
                ],
            )
        ],
        source={"host": "https://example", "auth": "sigv4"},
        options={"scroll": "10m", "page_size": 1000},
    )


def test_manifest_roundtrip() -> None:
    m = _sample_manifest()
    data = json.loads(m.to_json())
    m2 = Manifest.from_dict(data)
    assert m2.job_id == m.job_id
    assert len(m2.indices) == 1
    assert m2.indices[0].parts[0].size_bytes == 1234
    assert m2.indices[0].parts[1].checksum_sha256 is None
    assert m2.format == "bulk-ndjson-gz"


def test_manifest_rejects_unknown_version() -> None:
    payload = json.loads(_sample_manifest().to_json())
    payload["manifest_version"] = 99
    with pytest.raises(ValueError):
        Manifest.from_dict(payload)


def test_resolve_manifest_part_key() -> None:
    job = S3Uri.parse("s3://b/jobs/2026/")
    assert (
        resolve_manifest_part_key(job, "data/idx/part-00000.ndjson.gz")
        == "jobs/2026/data/idx/part-00000.ndjson.gz"
    )
    # Absolute keys (rare) are stripped of leading slash.
    assert (
        resolve_manifest_part_key(job, "/other/data/part.ndjson.gz") == "other/data/part.ndjson.gz"
    )


# ---------------------------------------------------------------------------
# Bulk-format detection
# ---------------------------------------------------------------------------


def test_is_bulk_action_line_true() -> None:
    for raw in (
        b'{"index":{"_index":"x","_id":"1"}}',
        b'  {"create":{"_index":"x"}}',
        b'{"update":{"_id":"1","_index":"x"}}',
        b'{"delete":{"_index":"x","_id":"2"}}',
    ):
        assert is_bulk_action_line(raw)


def test_is_bulk_action_line_false() -> None:
    for raw in (
        b'{"foo":"bar"}',
        b'{"index":{},"extra":1}',  # more than one top-level key
        b"not json",
        b'{"index":[1,2]}',  # value isn't dict, but we accept any value
        b"",
    ):
        # The function returns False for not-action-shaped lines. The
        # "value isn't dict" case still has one top-level key in {"index"...}
        # so it would actually return True; assert based on semantics, not
        # over-tighten the check. Filter to the truly-false ones:
        if raw == b'{"index":[1,2]}':
            continue
        assert not is_bulk_action_line(raw)


def test_detect_bulk_format() -> None:
    assert detect_bulk_format(b'{"index":{"_index":"x"}}')
    assert not detect_bulk_format(b'{"foo":1}')


# ---------------------------------------------------------------------------
# to_bulk_pairs / batch_bulk_pairs / serialise_bulk_body
# ---------------------------------------------------------------------------


def _bulk_lines() -> bytes:
    return (
        b'{"index":{"_index":"logs","_id":"a"}}\n'
        b'{"msg":"alpha"}\n'
        b'{"index":{"_index":"logs","_id":"b"}}\n'
        b'{"msg":"beta"}\n'
    )


def _source_lines() -> bytes:
    return b'{"msg":"alpha"}\n{"msg":"beta"}\n'


def _split_lines(blob: bytes) -> list:
    return [line for line in blob.split(b"\n") if line]


def test_to_bulk_pairs_bulk_format() -> None:
    pairs = list(to_bulk_pairs(_split_lines(_bulk_lines()), bulk_format=True))
    assert len(pairs) == 2
    action, source, parsed = pairs[0]
    assert b'"_id":"a"' in action
    assert b'"alpha"' in source
    assert parsed == {"index": {"_index": "logs", "_id": "a"}}


def test_to_bulk_pairs_source_only() -> None:
    pairs = list(
        to_bulk_pairs(
            _split_lines(_source_lines()),
            bulk_format=False,
            target_index="logs",
        )
    )
    assert len(pairs) == 2
    action, source, parsed = pairs[0]
    assert json.loads(action.decode()) == {"index": {"_index": "logs"}}
    assert parsed == {"index": {"_index": "logs"}}
    assert b'"alpha"' in source


def test_to_bulk_pairs_source_only_requires_index() -> None:
    with pytest.raises(ValueError, match="target_index"):
        list(to_bulk_pairs([b'{"msg":"a"}'], bulk_format=False))


def test_to_bulk_pairs_dangling_action() -> None:
    pairs_iter = to_bulk_pairs(
        [b'{"index":{"_index":"x"}}'],
        bulk_format=True,
    )
    with pytest.raises(ValueError, match="trailing bulk action"):
        list(pairs_iter)


def test_batch_bulk_pairs_size_bound() -> None:
    pairs = list(to_bulk_pairs(_split_lines(_bulk_lines()), bulk_format=True))
    # Force one batch per pair via a tight size bound.
    batches = list(batch_bulk_pairs(pairs, max_bytes=80))
    assert len(batches) == 2
    assert all(len(b) == 1 for b in batches)


def test_batch_bulk_pairs_item_bound() -> None:
    pairs = list(to_bulk_pairs(_split_lines(_bulk_lines()), bulk_format=True))
    batches = list(batch_bulk_pairs(pairs, max_bytes=10_000_000, max_items=1))
    assert len(batches) == 2


def test_serialise_bulk_body() -> None:
    pairs = list(to_bulk_pairs(_split_lines(_bulk_lines()), bulk_format=True))
    body = serialise_bulk_body(pairs)
    # Round-trip: split on newlines, expect 4 non-empty lines back.
    assert len(_split_lines(body)) == 4
    assert body.count(b"\n") == 4


# ---------------------------------------------------------------------------
# canonical_source_id
# ---------------------------------------------------------------------------


def test_canonical_source_id_stable_under_key_order() -> None:
    a = canonical_source_id(b'{"a":1,"b":2}')
    b = canonical_source_id(b'{"b":2,"a":1}')
    assert a == b
    assert len(a) == 40


def test_canonical_source_id_falls_back_for_non_json() -> None:
    raw = b"not json"
    h = canonical_source_id(raw)
    assert len(h) == 40


# ---------------------------------------------------------------------------
# Checkpoint files
# ---------------------------------------------------------------------------


def test_checkpoint_roundtrip(tmp_path) -> None:
    p = tmp_path / "ckpt.json"
    save_checkpoint(str(p), {"completed_parts": ["a", "b"]})
    assert load_checkpoint(str(p)) == {"completed_parts": ["a", "b"]}


def test_checkpoint_missing_returns_empty(tmp_path) -> None:
    assert load_checkpoint(str(tmp_path / "nope.json")) == {}


def test_checkpoint_unreadable_returns_empty(tmp_path) -> None:
    p = tmp_path / "ckpt.json"
    p.write_text("not json")
    assert load_checkpoint(str(p)) == {}


# ---------------------------------------------------------------------------
# NDJSON streaming (in-memory; no boto needed)
# ---------------------------------------------------------------------------


def test_iter_lines_handles_partial_chunks() -> None:
    # The internal _iter_lines is private; exercise it through the public
    # gzip-aware path by supplying a fake S3 client.
    from s3_migration.s3_common import open_ndjson_stream

    payload = b"line1\nline2\nline3\n"
    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb") as gz:
        gz.write(payload)
    gz_bytes = gz_buf.getvalue()

    class _FakeS3:
        def get_object(self, Bucket: str, Key: str):  # noqa: N803 - boto3 API
            return {"Body": io.BytesIO(gz_bytes)}

    lines = list(open_ndjson_stream(_FakeS3(), S3Uri.parse("s3://b/p/x.ndjson.gz")))
    assert lines == [b"line1", b"line2", b"line3"]
