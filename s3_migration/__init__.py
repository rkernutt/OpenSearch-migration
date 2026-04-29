"""S3-based bulk migration helpers.

Modules:
  s3_common      Shared helpers (S3 URI, manifest, gzipped NDJSON streaming, batching).
  s3_bulk_load   CLI: load gzipped NDJSON parts from S3 into Elasticsearch via _bulk.
  s3_extract    (planned) CLI: extract OpenSearch indices to gzipped NDJSON in S3.
  rfs_runner    (planned) CLI: thin wrapper around the upstream OpenSearch Migrations
                Reindex-from-Snapshot Docker image.

All HTTP and credential helpers are imported from the existing top-level scripts
(validate_migration.py / poll_reindex_task.py) — do not duplicate that logic here.
"""

__all__ = ["s3_common", "s3_bulk_load"]
