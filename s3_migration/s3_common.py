"""Shared helpers for the s3_migration package.

Pure-Python utilities (no HTTP, no CLI) that can be unit-tested in isolation:
  * `S3Uri`                  parse / join `s3://...` URIs
  * `Manifest` / `IndexEntry` / `PartEntry`   manifest schema (versioned)
  * `make_s3_client`         boto3 client factory (region / endpoint override)
  * `open_ndjson_stream`     stream lines from a remote NDJSON or NDJSON.gz object
  * `is_bulk_action_line`    detect Elasticsearch `_bulk` action header lines
  * `to_bulk_pairs`          convert raw lines into (action, source, parsed) tuples
  * `batch_bulk_pairs`       group tuples into size-bounded batches
  * `serialise_bulk_body`    bytes payload for the `_bulk` endpoint
  * `canonical_source_id`    SHA-1 of canonical source JSON (deterministic _id)
  * `load_checkpoint` / `save_checkpoint`   atomic local checkpoint files

All HTTP and credential helpers live in `validate_migration.py`; this module
deliberately stays free of `requests` so the tests can run without network
mocking libraries.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover - boto3 is in requirements.txt
    boto3 = None  # type: ignore[assignment]
    ClientError = Exception  # type: ignore[assignment,misc]

MANIFEST_NAME = "_manifest.json"
MANIFEST_VERSION = 1
DATA_PREFIX = "data"
DEFAULT_PART_SUFFIX = ".ndjson.gz"

_NDJSON_SUFFIXES = (".ndjson", ".ndjson.gz", ".jsonl", ".jsonl.gz")


# ---------------------------------------------------------------------------
# S3 URI
# ---------------------------------------------------------------------------

_S3_URI_RE = re.compile(r"^s3://([^/]+)(?:/(.*))?$")


@dataclass(frozen=True)
class S3Uri:
    """Parsed `s3://bucket/key` URI. ``key`` may be empty for bucket-root."""

    bucket: str
    key: str

    @classmethod
    def parse(cls, uri: str) -> "S3Uri":
        m = _S3_URI_RE.match((uri or "").strip())
        if not m:
            raise ValueError(f"not an s3:// URI: {uri!r}")
        bucket = m.group(1)
        key = (m.group(2) or "").lstrip("/")
        if not bucket:
            raise ValueError(f"missing bucket in S3 URI: {uri!r}")
        return cls(bucket=bucket, key=key)

    @property
    def is_prefix(self) -> bool:
        return self.key == "" or self.key.endswith("/")

    def join(self, *parts: str) -> "S3Uri":
        pieces = [p.strip("/") for p in parts if p]
        if not pieces:
            return self
        base = self.key.rstrip("/")
        joined = "/".join([base, *pieces]) if base else "/".join(pieces)
        return S3Uri(bucket=self.bucket, key=joined.lstrip("/"))

    def __str__(self) -> str:
        return f"s3://{self.bucket}/{self.key}" if self.key else f"s3://{self.bucket}"


# ---------------------------------------------------------------------------
# Manifest schema
# ---------------------------------------------------------------------------


@dataclass
class PartEntry:
    """A single NDJSON part inside an index entry."""

    key: str  # relative to the manifest's job URI (e.g. ``data/idx/part-00000.ndjson.gz``)
    size_bytes: int
    doc_count: int
    checksum_sha256: Optional[str] = None
    bulk_format: bool = True


@dataclass
class IndexEntry:
    name: str
    doc_count_source: Optional[int] = None
    parts: List[PartEntry] = field(default_factory=list)


@dataclass
class Manifest:
    job_id: str
    created_at: str
    indices: List[IndexEntry] = field(default_factory=list)
    source: Dict[str, Any] = field(default_factory=dict)
    options: Dict[str, Any] = field(default_factory=dict)
    manifest_version: int = MANIFEST_VERSION
    format: str = "bulk-ndjson-gz"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "job_id": self.job_id,
            "created_at": self.created_at,
            "format": self.format,
            "source": self.source,
            "indices": [
                {
                    "name": ie.name,
                    "doc_count_source": ie.doc_count_source,
                    "parts": [asdict(p) for p in ie.parts],
                }
                for ie in self.indices
            ],
            "options": self.options,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Manifest":
        version = int(data.get("manifest_version", MANIFEST_VERSION))
        if version != MANIFEST_VERSION:
            raise ValueError(
                f"unsupported manifest_version: {version!r} (expected {MANIFEST_VERSION})"
            )
        indices = [
            IndexEntry(
                name=ie["name"],
                doc_count_source=ie.get("doc_count_source"),
                parts=[PartEntry(**p) for p in ie.get("parts", [])],
            )
            for ie in data.get("indices", [])
        ]
        return cls(
            job_id=data["job_id"],
            created_at=data["created_at"],
            indices=indices,
            source=data.get("source", {}),
            options=data.get("options", {}),
            format=data.get("format", "bulk-ndjson-gz"),
            manifest_version=version,
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False)


def manifest_uri(job_uri: S3Uri) -> S3Uri:
    return job_uri.join(MANIFEST_NAME)


def resolve_manifest_part_key(job_uri: S3Uri, manifest_key: str) -> str:
    """Manifest part keys may be relative to the job URI or already absolute."""
    if manifest_key.startswith("/"):
        return manifest_key.lstrip("/")
    base = job_uri.key.rstrip("/") + "/" if job_uri.key else ""
    return base + manifest_key


# ---------------------------------------------------------------------------
# S3 client wrapper (thin, no caching — caller passes the client around)
# ---------------------------------------------------------------------------


def make_s3_client(
    region: Optional[str] = None,
    endpoint_url: Optional[str] = None,
) -> Any:
    if boto3 is None:
        raise RuntimeError("boto3 is required for S3 operations; install boto3>=1.26,<2")
    return boto3.client("s3", region_name=region, endpoint_url=endpoint_url)


def s3_object_exists(s3: Any, uri: S3Uri) -> bool:
    try:
        s3.head_object(Bucket=uri.bucket, Key=uri.key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "") if hasattr(e, "response") else ""
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def s3_get_bytes(s3: Any, uri: S3Uri) -> bytes:
    obj = s3.get_object(Bucket=uri.bucket, Key=uri.key)
    return obj["Body"].read()


def s3_put_bytes(
    s3: Any,
    uri: S3Uri,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> None:
    s3.put_object(Bucket=uri.bucket, Key=uri.key, Body=data, ContentType=content_type)


def load_manifest(s3: Any, job_uri: S3Uri) -> Optional[Manifest]:
    muri = manifest_uri(job_uri)
    if not s3_object_exists(s3, muri):
        return None
    data = json.loads(s3_get_bytes(s3, muri).decode("utf-8"))
    return Manifest.from_dict(data)


def save_manifest(s3: Any, job_uri: S3Uri, manifest: Manifest) -> None:
    muri = manifest_uri(job_uri)
    s3_put_bytes(s3, muri, manifest.to_json().encode("utf-8"), content_type="application/json")


def list_ndjson_parts(s3: Any, prefix_uri: S3Uri) -> List[Tuple[S3Uri, int]]:
    """List `*.ndjson(.gz)` and `*.jsonl(.gz)` parts under a prefix.

    Returns a sorted list of ``(S3Uri, size_bytes)`` tuples.
    """
    paginator = s3.get_paginator("list_objects_v2")
    results: List[Tuple[S3Uri, int]] = []
    if prefix_uri.key and not prefix_uri.key.endswith("/"):
        prefix = prefix_uri.key + "/"
    else:
        prefix = prefix_uri.key
    for page in paginator.paginate(Bucket=prefix_uri.bucket, Prefix=prefix):
        for entry in page.get("Contents", []) or []:
            k = entry["Key"]
            if any(k.endswith(suffix) for suffix in _NDJSON_SUFFIXES):
                results.append((S3Uri(bucket=prefix_uri.bucket, key=k), int(entry.get("Size", 0))))
    results.sort(key=lambda t: t[0].key)
    return results


# ---------------------------------------------------------------------------
# NDJSON streaming
# ---------------------------------------------------------------------------


def open_ndjson_stream(s3: Any, uri: S3Uri) -> Iterator[bytes]:
    """Yield raw line bytes (without trailing newline) from an NDJSON object.

    Handles `.gz` transparently. Streams the body so memory use stays bounded
    even for multi-GB parts.
    """
    obj = s3.get_object(Bucket=uri.bucket, Key=uri.key)
    body = obj["Body"]
    if uri.key.endswith(".gz"):
        with gzip.GzipFile(fileobj=body) as gz:
            yield from _iter_lines(gz)
    else:
        yield from _iter_lines(body)


def _iter_lines(stream: Any) -> Iterator[bytes]:
    buf = b""
    while True:
        chunk = stream.read(65536)
        if not chunk:
            if buf:
                yield buf
            return
        buf += chunk
        while True:
            nl = buf.find(b"\n")
            if nl < 0:
                break
            line = buf[:nl]
            buf = buf[nl + 1 :]
            yield line


# ---------------------------------------------------------------------------
# Bulk batching helpers
# ---------------------------------------------------------------------------


_BULK_ACTION_KEYS = ("index", "create", "update", "delete")


def is_bulk_action_line(raw: bytes) -> bool:
    """Return True if *raw* looks like an Elasticsearch `_bulk` action header."""
    s = raw.lstrip()
    if not s.startswith(b"{"):
        return False
    try:
        obj = json.loads(s.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(obj, dict) or len(obj) != 1:
        return False
    return next(iter(obj)) in _BULK_ACTION_KEYS


def detect_bulk_format(first_nonblank_line: bytes) -> bool:
    """True if the first non-blank line of a part looks like a bulk action header."""
    return is_bulk_action_line(first_nonblank_line)


def to_bulk_pairs(
    lines: Iterable[bytes],
    bulk_format: bool,
    target_index: Optional[str] = None,
) -> Iterator[Tuple[bytes, bytes, Dict[str, Any]]]:
    """Yield ``(action_line, source_line, parsed_action)`` tuples.

    bulk_format=True
        Each consecutive non-blank pair of lines is ``(action, source)``.
    bulk_format=False
        Each non-blank line is a source document; a synthetic
        ``{"index": {"_index": target_index}}`` action is generated.
    """
    if bulk_format:
        action: Optional[bytes] = None
        for raw in lines:
            if not raw.strip():
                continue
            if action is None:
                action = raw
                continue
            try:
                parsed = json.loads(action.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                raise ValueError(f"invalid bulk action line: {action!r} ({e})") from e
            yield action, raw, parsed
            action = None
        if action is not None:
            raise ValueError("trailing bulk action with no source document")
    else:
        if not target_index:
            raise ValueError("source-only NDJSON requires target_index")
        action_template: Dict[str, Any] = {"index": {"_index": target_index}}
        action_bytes = json.dumps(action_template, separators=(",", ":")).encode("utf-8")
        for raw in lines:
            if not raw.strip():
                continue
            yield action_bytes, raw, action_template


def batch_bulk_pairs(
    pairs: Iterable[Tuple[bytes, bytes, Dict[str, Any]]],
    max_bytes: int,
    max_items: int = 5000,
) -> Iterator[List[Tuple[bytes, bytes, Dict[str, Any]]]]:
    """Group ``(action, source, parsed)`` tuples into size- and count-bounded batches."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if max_items <= 0:
        raise ValueError("max_items must be positive")
    batch: List[Tuple[bytes, bytes, Dict[str, Any]]] = []
    size = 0
    for pair in pairs:
        addition = len(pair[0]) + len(pair[1]) + 2  # two trailing newlines
        if batch and (size + addition > max_bytes or len(batch) >= max_items):
            yield batch
            batch = []
            size = 0
        batch.append(pair)
        size += addition
    if batch:
        yield batch


def serialise_bulk_body(batch: List[Tuple[bytes, bytes, Dict[str, Any]]]) -> bytes:
    """Serialise a batch of pairs into the bytes payload accepted by `_bulk`."""
    buf = io.BytesIO()
    for action, source, _parsed in batch:
        buf.write(action)
        buf.write(b"\n")
        buf.write(source)
        buf.write(b"\n")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Hashing / id helpers
# ---------------------------------------------------------------------------


def canonical_source_id(source_bytes: bytes) -> str:
    """Deterministic id derived from a source JSON document.

    SHA-1 over the canonicalised JSON (sorted keys, no whitespace). Falls back
    to the raw bytes if the input is not valid JSON.
    """
    try:
        obj = json.loads(source_bytes.decode("utf-8"))
        canon = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (ValueError, UnicodeDecodeError):
        canon = source_bytes
    return hashlib.sha1(canon).hexdigest()


# ---------------------------------------------------------------------------
# Local checkpoint files
# ---------------------------------------------------------------------------


def load_checkpoint(path: str) -> Dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_checkpoint(path: str, data: Dict[str, Any]) -> None:
    if not path:
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
