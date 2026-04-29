# Serverless: Elastic Cloud and Amazon OpenSearch Serverless

**Last reviewed:** 2026-03-18 — verify periodically against [Elastic Cloud Serverless](https://www.elastic.co/docs/deploy-manage/deploy/cloud-on-prem/cloud) and [AWS OpenSearch Serverless](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless.html) docs (API limits and feature matrix change).

## Elastic Cloud Serverless (destination)

**Remote reindex is not supported** on Elastic Cloud Serverless deployments. The Elasticsearch `_reindex` API with `source.remote` is not available for this product model.

**Use one of these data paths instead:**

- **S3 staging** (`s3_migration.s3_extract` → `s3_migration.s3_bulk_load`) — see [S3_MIGRATION.md](S3_MIGRATION.md) and [RUNBOOK.md](../RUNBOOK.md) Option D. Works for any source reachable by basic auth, SigV4, or the in-repo proxy; the loader posts to Serverless via API key.
- **Reindex-from-Snapshot** via wrapped upstream RFS (`s3_migration.rfs_runner`) — see [RFS.md](RFS.md) and [RUNBOOK.md](../RUNBOOK.md) Option E. Supports `--target-type ELASTICSEARCH_SERVERLESS` natively, including settings sanitization and hidden-index renaming. For multi-worker fan-out, [`iac/terraform/rfs-orchestration/`](../iac/terraform/rfs-orchestration/) provisions a Step Functions Map state on top of the rfs-fargate task definition.
- **Logstash** — read from OpenSearch with a public endpoint, fine-grained credentials, or the [Proxy](../Proxy/README.md) pattern for VPC; write to Serverless using the Elasticsearch output with your Serverless URL and API key. See [RUNBOOK.md](../RUNBOOK.md) Option B and [Logstash_input/README.md](../Logstash_input/README.md).

### Pre-load: metadata migration with sanitization

Serverless rejects most index settings (shards, replicas, translog, merge, store, allocation, …). Run [`metadata_migration`](../metadata_migration/) **before** the data path so templates / component templates / ingest pipelines arrive on Serverless with the forbidden settings stripped and any ES 5/6 multi-type mappings flattened to typeless single-doc mappings:

```bash
migrate metadata \
  --source-host "$SOURCE_OPENSEARCH_HOST" \
  --source-user "$SOURCE_OPENSEARCH_USER" --source-password "$SOURCE_OPENSEARCH_PASSWORD" \
  --dest-host "$DEST_ELASTIC_HOST" --dest-api-key "$DEST_ELASTIC_API_KEY" \
  --target-type ELASTICSEARCH_SERVERLESS \
  --include templates,index_templates,component_templates,ingest_pipelines \
  --strict-exit-codes --log-format json
```

See [METADATA_MIGRATION.md](METADATA_MIGRATION.md) for the full Serverless-forbidden-settings list, mapping translation matrix, and per-object reports. The sanitizer is also exposed as a standalone CLI for one-off JSON files: `migrate sanitize --mode index-body --target-type ELASTICSEARCH_SERVERLESS < my-template.json`.

### Validation + cutover gates

Validate counts and sample documents on the Serverless host with `validate_migration.py` (counts and `_mget` work the same way). Then, before flipping traffic:

- **Curated query parity:** [`shadow_diff.py`](../shadow_diff.py) replays a list of saved queries against source + dest, comparing counts, top-K hit IDs (Jaccard), and per-hit canonical-JSON SHA-256s. Strict exit codes for CI gating. See [SHADOW_DIFF.md](SHADOW_DIFF.md).
- **Sampled real-traffic parity (optional):** Enable capture mode on the proxy (`PROXY_CAPTURE_MODE=local|s3`), record real or synthetic traffic, then `migrate replay --captures ... --dest-host ...` to replay a sampled subset against the Serverless destination. See [CAPTURE_REPLAY.md](CAPTURE_REPLAY.md).

---

## Amazon OpenSearch Serverless (source)

Amazon OpenSearch **Serverless** uses a different IAM and networking model than provisioned Amazon OpenSearch Service domains:

- Collections and data access policies replace the classic “domain + FGAC” model for many operations.
- Endpoints and signing can differ from provisioned `es.amazonaws.com` domains.

**Practical approach for migration to Elastic:**

1. **Prefer Logstash** (or another client that can sign requests with the correct SigV4 scope and policy for Serverless) as the replication path, similar to IAM-only provisioned domains. You may need a small adapter or proxy if your client does not support Serverless signing.
2. **Remote reindex from Elastic Hosted to a Serverless source** is uncommon and not covered in detail here; confirm network reachability and compatible authentication with AWS documentation before relying on it.
3. **Scroll / search** behavior and index listing should be tested at your expected volume; treat Serverless as a separate integration that you validate in a dev collection first.

For Elastic Cloud **Hosted**, remote reindex from a **provisioned** OpenSearch domain remains the primary API-driven path documented in [Remote_Reindex/README.md](../Remote_Reindex/README.md).
