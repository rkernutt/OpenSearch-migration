"""SigV4 reverse proxy with optional request/response capture.

Modules:
  app      Flask reverse proxy (entrypoint: ``python -m Proxy.app``).
  capture  NDJSON request/response capture (local or S3) used by the
           replay tooling. See ``docs/CAPTURE_REPLAY.md``.
"""

__all__ = ["capture"]
