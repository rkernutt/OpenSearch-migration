"""Request/response capture for the SigV4 proxy.

When enabled (``PROXY_CAPTURE_MODE=local`` or ``s3``) every proxied
request and its response is appended to a rolling NDJSON file. The
captured records can later be replayed with
:mod:`replay.replayer` against a different cluster (typically the
destination during cutover validation).

Design constraints
------------------
* **Capture must never block the proxy.** A background worker thread
  flushes records; the request handler only enqueues. Bounded queue
  drops the oldest records (with a counter) if the writer can't keep
  up.
* **Failures must never break the proxy response.** Every recorded
  exception is logged and counted; the request still succeeds.
* **Bodies are bounded.** ``PROXY_CAPTURE_MAX_BODY_BYTES`` (default
  1 MiB) caps how much of each request/response body is stored
  inline. Anything larger keeps just a SHA-256 hash + size — useful
  for tracing without ballooning capture volume.
* **Headers are redacted.** Authorization, cookies, API keys are
  stripped before writing.

Scope
-----
This is a Python, document-level capture path: good for sampled
cutover validation. It is *not* a Kafka-backed zero-loss traffic
mirror — for that, use upstream OpenSearch Migrations.
"""

from __future__ import annotations

import dataclasses
import datetime
import gzip
import hashlib
import io
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_log = logging.getLogger("proxy.capture")

# Headers always stripped before storage.
_REDACT_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-amz-security-token",
    "x-elastic-product-origin",
    "es-secondary-authorization",
    "x-api-key",
    "proxy-authorization",
}


def _utc_now_iso() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sha256_hex(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _REDACT_HEADERS:
            continue
        out[k] = v
    return out


def _maybe_inline(b: bytes, max_bytes: int) -> Tuple[Optional[str], int, str]:
    """Return ``(inline_text_or_None, len, sha256_hex)``."""
    if not b:
        return "", 0, "sha256:" + hashlib.sha256(b"").hexdigest()
    n = len(b)
    h = _sha256_hex(b)
    if n > max_bytes:
        return None, n, h
    try:
        return b.decode("utf-8"), n, h
    except UnicodeDecodeError:
        return None, n, h


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CaptureConfig:
    mode: str  # "off", "local", or "s3"
    local_dir: Optional[Path] = None
    s3_uri: Optional[str] = None
    s3_region: str = "us-east-1"
    include_bodies: bool = True
    max_body_bytes: int = 1 * 1024 * 1024  # 1 MiB
    rotate_bytes: int = 100 * 1024 * 1024  # 100 MiB per file
    rotate_seconds: int = 60
    queue_capacity: int = 10_000
    path_include: List[str] = dataclasses.field(default_factory=lambda: [".*"])
    path_exclude: List[str] = dataclasses.field(default_factory=list)
    methods: List[str] = dataclasses.field(default_factory=lambda: ["GET", "POST", "HEAD"])

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "CaptureConfig":
        env = env if env is not None else dict(os.environ)
        mode = env.get("PROXY_CAPTURE_MODE", "off").lower()
        cfg = cls(mode=mode)
        if mode == "off":
            return cfg
        cfg.local_dir = Path(env["PROXY_CAPTURE_DIR"]) if env.get("PROXY_CAPTURE_DIR") else None
        cfg.s3_uri = env.get("PROXY_CAPTURE_S3_URI") or None
        cfg.s3_region = env.get("AWS_REGION", "us-east-1")
        cfg.include_bodies = env.get("PROXY_CAPTURE_INCLUDE_BODIES", "true").lower() in (
            "1",
            "true",
            "yes",
        )
        cfg.max_body_bytes = int(env.get("PROXY_CAPTURE_MAX_BODY_BYTES", str(cfg.max_body_bytes)))
        cfg.rotate_bytes = int(env.get("PROXY_CAPTURE_ROTATE_BYTES", str(cfg.rotate_bytes)))
        cfg.rotate_seconds = int(env.get("PROXY_CAPTURE_ROTATE_SECONDS", str(cfg.rotate_seconds)))
        cfg.queue_capacity = int(env.get("PROXY_CAPTURE_QUEUE", str(cfg.queue_capacity)))
        if env.get("PROXY_CAPTURE_PATH_INCLUDE"):
            cfg.path_include = [
                p.strip() for p in env["PROXY_CAPTURE_PATH_INCLUDE"].split(",") if p.strip()
            ]
        if env.get("PROXY_CAPTURE_PATH_EXCLUDE"):
            cfg.path_exclude = [
                p.strip() for p in env["PROXY_CAPTURE_PATH_EXCLUDE"].split(",") if p.strip()
            ]
        if env.get("PROXY_CAPTURE_METHODS"):
            cfg.methods = [
                m.strip().upper() for m in env["PROXY_CAPTURE_METHODS"].split(",") if m.strip()
            ]
        return cfg

    def __post_init__(self) -> None:
        if self.mode not in ("off", "local", "s3"):
            raise ValueError(f"PROXY_CAPTURE_MODE invalid: {self.mode!r}")


# ---------------------------------------------------------------------------
# Capturer
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CaptureRecord:
    ts: str
    request_id: str
    method: str
    path: str
    query_string: str
    request_headers: Dict[str, str]
    request_body: Optional[str]
    request_body_bytes: int
    request_body_hash: str
    response_status: int
    response_headers: Dict[str, str]
    response_body: Optional[str]
    response_body_bytes: int
    response_body_hash: str
    latency_ms: int
    target_host: str

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), separators=(",", ":"))


class Capturer:
    """Drains a queue of records and writes them to local disk or S3.

    Use :meth:`record` from the request handler — it never raises and
    returns immediately (puts the record on a bounded queue).
    """

    def __init__(self, cfg: CaptureConfig) -> None:
        self.cfg = cfg
        self._q: "queue.Queue[Optional[CaptureRecord]]" = queue.Queue(maxsize=cfg.queue_capacity)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.dropped = 0
        self.errors = 0
        self.written = 0
        self._include_re = [re.compile(p) for p in cfg.path_include] if cfg.path_include else None
        self._exclude_re = [re.compile(p) for p in cfg.path_exclude] if cfg.path_exclude else None
        self._allowed_methods = {m.upper() for m in cfg.methods}

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self.cfg.mode == "off" or self._thread is not None:
            return
        if self.cfg.mode == "local":
            if not self.cfg.local_dir:
                raise ValueError("PROXY_CAPTURE_DIR required when mode=local")
            self.cfg.local_dir.mkdir(parents=True, exist_ok=True)
            self._thread = threading.Thread(
                target=self._run_local, name="proxy-capture-local", daemon=True
            )
        else:
            if not self.cfg.s3_uri:
                raise ValueError("PROXY_CAPTURE_S3_URI required when mode=s3")
            self._thread = threading.Thread(
                target=self._run_s3, name="proxy-capture-s3", daemon=True
            )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is None:
            return
        self._stop.set()
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=timeout)
        self._thread = None

    # -- public hook used by request handler ----------------------------

    def record(self, rec: CaptureRecord) -> None:
        if self.cfg.mode == "off":
            return
        if rec.method.upper() not in self._allowed_methods:
            return
        # Path filtering
        if self._include_re and not any(r.search(rec.path) for r in self._include_re):
            return
        if self._exclude_re and any(r.search(rec.path) for r in self._exclude_re):
            return
        try:
            self._q.put_nowait(rec)
        except queue.Full:
            self.dropped += 1

    # -- worker loops ---------------------------------------------------

    def _run_local(self) -> None:
        current_path: Optional[Path] = None
        current_size = 0
        opened_at = time.monotonic()
        f: Optional[io.IOBase] = None

        def _open_new() -> Tuple[Path, io.IOBase, float]:
            assert self.cfg.local_dir is not None
            ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
            path = self.cfg.local_dir / f"capture-{ts}-{os.getpid()}-{uuid.uuid4().hex[:6]}.ndjson"
            return path, path.open("a", encoding="utf-8", buffering=1), time.monotonic()

        try:
            while True:
                try:
                    item = self._q.get(timeout=1.0)
                except queue.Empty:
                    if self._stop.is_set() and self._q.empty():
                        break
                    continue
                if item is None:
                    break
                line = item.to_json() + "\n"
                blen = len(line.encode("utf-8"))
                if (
                    f is None
                    or current_size + blen > self.cfg.rotate_bytes
                    or (time.monotonic() - opened_at) >= self.cfg.rotate_seconds
                ):
                    if f is not None:
                        try:
                            f.close()
                        except Exception:  # noqa: BLE001
                            pass
                    current_path, f, opened_at = _open_new()
                    current_size = 0
                try:
                    f.write(line)
                    current_size += blen
                    self.written += 1
                except Exception:  # noqa: BLE001
                    self.errors += 1
                    _log.exception("local capture write failed (path=%s)", current_path)
        finally:
            if f is not None:
                try:
                    f.close()
                except Exception:  # noqa: BLE001
                    pass

    def _run_s3(self) -> None:
        # Lazy import: keeps boto3 out of the hot path for users not using S3.
        from urllib.parse import urlparse

        import boto3  # type: ignore[import-not-found]

        u = urlparse(self.cfg.s3_uri or "")
        if u.scheme != "s3" or not u.netloc:
            raise ValueError(f"PROXY_CAPTURE_S3_URI invalid: {self.cfg.s3_uri}")
        bucket = u.netloc
        prefix = (u.path or "/").lstrip("/")
        client = boto3.client("s3", region_name=self.cfg.s3_region)

        buf = io.BytesIO()
        gz = gzip.GzipFile(fileobj=buf, mode="wb")
        opened_at = time.monotonic()
        records_in_buf = 0

        def _flush() -> None:
            nonlocal buf, gz, opened_at, records_in_buf
            if records_in_buf == 0:
                return
            gz.close()
            buf.seek(0)
            ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            key = f"{prefix.rstrip('/')}/capture-{ts}-{uuid.uuid4().hex[:8]}.ndjson.gz"
            try:
                client.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())
                self.written += records_in_buf
            except Exception:  # noqa: BLE001
                self.errors += 1
                _log.exception("s3 capture put_object failed (key=%s)", key)
            buf = io.BytesIO()
            gz = gzip.GzipFile(fileobj=buf, mode="wb")
            opened_at = time.monotonic()
            records_in_buf = 0

        try:
            while True:
                try:
                    item = self._q.get(timeout=1.0)
                except queue.Empty:
                    if self._stop.is_set() and self._q.empty():
                        break
                    if (time.monotonic() - opened_at) >= self.cfg.rotate_seconds:
                        _flush()
                    continue
                if item is None:
                    break
                line = (item.to_json() + "\n").encode("utf-8")
                gz.write(line)
                records_in_buf += 1
                # Approx size check using uncompressed buffer.
                if (
                    records_in_buf >= 1000
                    or (time.monotonic() - opened_at) >= self.cfg.rotate_seconds
                ):
                    _flush()
        finally:
            _flush()


# ---------------------------------------------------------------------------
# Helper used by the proxy
# ---------------------------------------------------------------------------


def make_record(
    *,
    method: str,
    path: str,
    query_string: str,
    request_headers: Dict[str, str],
    request_body: bytes,
    response_status: int,
    response_headers: Dict[str, str],
    response_body: bytes,
    latency_ms: int,
    target_host: str,
    include_bodies: bool,
    max_body_bytes: int,
) -> CaptureRecord:
    req_inline, req_len, req_hash = _maybe_inline(request_body, max_body_bytes)
    resp_inline, resp_len, resp_hash = _maybe_inline(response_body, max_body_bytes)
    if not include_bodies:
        req_inline = None
        resp_inline = None
    return CaptureRecord(
        ts=_utc_now_iso(),
        request_id=str(uuid.uuid4()),
        method=method.upper(),
        path=path,
        query_string=query_string,
        request_headers=_redact_headers(request_headers),
        request_body=req_inline,
        request_body_bytes=req_len,
        request_body_hash=req_hash,
        response_status=response_status,
        response_headers=_redact_headers(response_headers),
        response_body=resp_inline,
        response_body_bytes=resp_len,
        response_body_hash=resp_hash,
        latency_ms=latency_ms,
        target_host=target_host,
    )


__all__ = ["CaptureConfig", "CaptureRecord", "Capturer", "make_record"]
