# `s3_migration` — S3-staged migration

Bulk-migration tooling that uses S3 as a staging area between OpenSearch and
Elastic Cloud (Hosted or Serverless). Useful when:

- The source OpenSearch domain cannot be reached directly from Elastic
  (VPC-only, restricted egress, air-gapped).
- You want to decouple **extract** (OpenSearch → S3) from **load**
  (S3 → Elastic) so they can be paused, resumed, replayed, or run by
  different operators.
- You already have NDJSON dumps in S3 (partner data, archived exports,
  `elasticdump` output) and need to load them into Elastic.

## Components

| Module | Status | Purpose |
|--------|--------|---------|
| [`s3_common.py`](s3_common.py) | shipped (phase 1) | Shared helpers: `S3Uri`, `Manifest`, gzip NDJSON streaming, bulk batching. |
| [`s3_bulk_load.py`](s3_bulk_load.py) | shipped (phase 1) | CLI: stream `*.ndjson(.gz)` parts from S3 and bulk-index into Elasticsearch. |
| [`s3_extract.py`](s3_extract.py) | shipped (phase 2) | CLI: sliced-scroll OpenSearch into gzipped NDJSON parts in S3, with manifest + checkpoint. |
| [`rfs_runner.py`](rfs_runner.py) | shipped (phase 4) | Thin wrapper around the upstream OpenSearch Migrations RFS container; auto-runs `validate_migration.py` afterwards. Pairs with [`iac/terraform/rfs-fargate/`](../iac/terraform/rfs-fargate/) for single-task AWS deployments and [`iac/terraform/rfs-orchestration/`](../iac/terraform/rfs-orchestration/) for multi-worker fan-out via Step Functions. |

## Object format

Each "job" lives under one S3 prefix:

```
s3://bucket/prefix/<job-id>/
  _manifest.json
  data/<index>/part-00000.ndjson.gz
  data/<index>/part-00001.ndjson.gz
  dlq/<job-id>/...                  # only if loader writes per-document failures
```

Each `*.ndjson.gz` part contains the exact bytes consumed by Elasticsearch's
`_bulk` API: alternating action and source lines (one JSON object per line).
The loader also accepts plain "one source document per line" NDJSON, in which
case `--target-index` must be supplied.

The manifest records source host, per-index `_count`, and per-part doc count
and SHA-256, so the loader can reconcile and validate after a load.

## Quick start (full S3 staging path)

```bash
# 1. Extract from OpenSearch to S3 (basic auth example; SigV4 is the default
#    when --source-user/--source-password are not supplied).
python -m s3_migration.s3_extract \
  --source-host "$SOURCE_OPENSEARCH_HOST" \
  --source-user "$SOURCE_OPENSEARCH_USER" \
  --source-password "$SOURCE_OPENSEARCH_PASSWORD" \
  --indices "logs-2024,metrics-2024" \
  --s3-uri s3://my-bucket/migration/2026-04-29/ \
  --slices 4 --strict-exit-codes --log-format json \
  --checkpoint-file ./.extract.ckpt

# 2. Load the same prefix into Elastic (Cloud Hosted or Serverless).
python -m s3_migration.s3_bulk_load \
  --s3-uri s3://my-bucket/migration/2026-04-29/ \
  --dest-host "$DEST_ELASTIC_HOST" \
  --dest-api-key "$DEST_ELASTIC_API_KEY" \
  --strict-exit-codes --log-format json

# 3. Validate counts (existing toolkit script; Serverless-compatible).
python validate_migration.py \
  --indices "logs-2024,metrics-2024" \
  --check-existence --sample-size 50
```

Routing through the in-repo SigV4 reverse proxy is just basic auth against the
proxy URL. Add `--via-proxy` so the manifest records ``"auth": "proxy"`` for
audit:

```bash
python -m s3_migration.s3_extract \
  --source-host http://proxy.internal:9200 \
  --source-user "$PROXY_USER" --source-password "$PROXY_PASSWORD" \
  --via-proxy ...
```

## Pair with

- [`metadata_migration/`](../metadata_migration/) — copy templates / component templates / ingest pipelines **before** the data load, with optional Serverless settings sanitization and ES 5/6 multi-type mapping flatten. See [`docs/METADATA_MIGRATION.md`](../docs/METADATA_MIGRATION.md).
- [`shadow_diff.py`](../shadow_diff.py) and [`replay/`](../replay/) — cutover gates; replay curated queries and/or sampled real traffic against the destination before flipping production.

## Documentation

- [`docs/S3_MIGRATION.md`](../docs/S3_MIGRATION.md) — architecture, runbook, and troubleshooting for the S3 staging path.
- [`docs/RFS.md`](../docs/RFS.md) — runbook for the wrapped Reindex-from-Snapshot path.
- [`docs/METADATA_MIGRATION.md`](../docs/METADATA_MIGRATION.md) — companion runbook for templates / pipelines / sanitization.
- [`docs/AUTOMATION.md`](../docs/AUTOMATION.md) — exit codes and `make` targets.
- [`docs/TOOLS.md`](../docs/TOOLS.md) — single-page index of every CLI in the repo.
