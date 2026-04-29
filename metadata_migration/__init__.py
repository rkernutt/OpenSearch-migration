"""Metadata migration: copy index templates, component templates, ingest
pipelines, and similar cluster-level objects from OpenSearch to Elastic with
optional sanitization for Serverless and multi-version compatibility.

Modules:
  sanitizer  Pure-Python helpers: ``sanitize_index_settings`` and
             ``sanitize_mapping``. Used by the migrator and exposed as a CLI
             so users can sanitize standalone JSON files (e.g. before
             pre-creating a destination index for the S3 staging or remote
             reindex paths).
  migrator   CLI: read a list of templates / pipelines from the source,
             sanitize them, and POST them to the destination.

These tools deliberately do *not* depend on `s3_migration`; they only use the
shared HTTP / auth / logging helpers from `validate_migration.py` so the
behaviour is identical to the rest of the toolkit.
"""

__all__ = ["sanitizer", "migrator"]
