"""Replay captured proxy traffic against a destination cluster.

The :mod:`replay.replayer` CLI reads NDJSON capture files produced by
:mod:`Proxy.capture` (local or S3) and replays each request against an
Elasticsearch / Elastic Cloud destination. Optionally compares the
response status and body to the captured original — useful as the final
shadow check during a cutover.

This is the Python, document-level counterpart to upstream OpenSearch
Migrations' Java capture/replay pipeline. Suitable for sampled cutover
validation, not high-fidelity petabyte-scale traffic mirroring.
"""

__all__ = ["replayer"]
