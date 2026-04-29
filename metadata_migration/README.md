# `metadata_migration` — templates, pipelines, and sanitizers

Cluster-level metadata migrator and pure-Python sanitizers for moving index
templates, component templates, ingest pipelines, and similar objects from
OpenSearch (or any Elasticsearch-compatible cluster) to Elastic Cloud (Hosted
or Serverless), with optional Serverless settings stripping and ES 5/6
multi-type mapping flatten.

Run this **before** any data path (S3 staging, RFS, Logstash, remote reindex)
so destination indices are created with the right settings and mappings.

## Components

| Module | Purpose |
|--------|---------|
| [`sanitizer.py`](sanitizer.py) | Pure-Python helpers: `sanitize_index_settings`, `sanitize_mapping`. Also a small CLI for sanitizing standalone JSON files (e.g. before pre-creating a destination index). |
| [`migrator.py`](migrator.py) | CLI: list templates / pipelines on the source, sanitize, and PUT to the destination with optional dry-run, glob filters, and per-object reports. |

Both reuse the HTTP / auth / logging helpers from `validate_migration.py`, so
behaviour and exit codes match the rest of the toolkit.

## What gets migrated

| Object kind | Source endpoint | Destination endpoint |
|-------------|-----------------|----------------------|
| Legacy index templates | `GET _template/*` | `PUT _template/<name>` |
| Composable index templates | `GET _index_template/*` | `PUT _index_template/<name>` |
| Component templates | `GET _component_template/*` | `PUT _component_template/<name>` |
| Ingest pipelines | `GET _ingest/pipeline/*` | `PUT _ingest/pipeline/<id>` |

System objects (`.security`, `.slm-history`, `.kibana_*`, etc.) are skipped by
default — pass `--keep-system-objects` to override.

## What gets sanitized

### Settings (`sanitize_index_settings`)

Stripped when `--target-type ELASTICSEARCH_SERVERLESS`:

- `index.number_of_shards`, `index.number_of_replicas`, `index.refresh_interval`
- `index.translog.*`, `index.merge.*`, `index.store.*`
- `index.routing.*`, `index.shard.*`, `index.allocation.*`
- `index.codec`, `index.soft_deletes.*`, `index.search.idle.*`
- `index.write.*`, `index.search.slowlog.*`, `index.indexing.slowlog.*`

Always stripped (OpenSearch-only / deprecated):

- `index.knn`, `index.knn.algo_param.*`, `index.plugins.*`
- `index.codec.zstd_*`, `index.codec.qat_*`

### Mappings (`sanitize_mapping`)

- **ES 5/6 multi-type → typeless flatten:** if a mapping has multiple top-level
  type keys (or an explicit `_default_`), the migrator merges them into a single
  typeless mapping, preferring the non-`_default_` type.
- **`string` → `text`/`keyword`:** any field with `type: string` is rewritten
  to `keyword` (when `index: not_analyzed`) or `text` otherwise; `analyzer` is
  preserved.
- **Deprecated top-level options removed:** `_all`, `_timestamp`, `_ttl`,
  `_size`, `_field_names.enabled`, etc.

The full matrix is in [`docs/METADATA_MIGRATION.md`](../docs/METADATA_MIGRATION.md).

## Quick start

```bash
# Migrate everything (templates, component templates, index_templates, ingest pipelines)
# from OpenSearch to Elastic Cloud Serverless, with sanitization and dry-run first.
migrate metadata \
  --source-host "$SOURCE_OPENSEARCH_HOST" \
  --source-user "$SOURCE_OPENSEARCH_USER" \
  --source-password "$SOURCE_OPENSEARCH_PASSWORD" \
  --dest-host "$DEST_ELASTIC_HOST" \
  --dest-api-key "$DEST_ELASTIC_API_KEY" \
  --target-type ELASTICSEARCH_SERVERLESS \
  --include templates,index_templates,component_templates,ingest_pipelines \
  --report-dir ./metadata-report \
  --dry-run --strict-exit-codes --log-format json

# Inspect ./metadata-report/*.json, then re-run without --dry-run to apply.
```

Glob filters (work on the object name, not on the type):

```bash
migrate metadata --include templates --name 'logs-*' --exclude '*-old' ...
```

Pre-create a destination index from a sanitized template body:

```bash
migrate sanitize --mode index-body --target-type ELASTICSEARCH_SERVERLESS \
  < my-template.json > my-template.serverless.json

curl -XPUT -H "Authorization: ApiKey $DEST_ELASTIC_API_KEY" \
  "$DEST_ELASTIC_HOST/my-index" \
  -H 'content-type: application/json' \
  --data-binary @my-template.serverless.json
```

## Exit codes (`--strict-exit-codes`)

Same convention as the rest of the toolkit:

| Code | Meaning |
|------|---------|
| `0` | All requested objects migrated (or, in `--dry-run`, would migrate) successfully. |
| `2` | Configuration error (missing host, unreadable file, bad `--include`, etc.). |
| `3` | Transport / auth / TLS failure on source or destination. |
| `4` | At least one object failed to PUT after sanitization (per-object report shows which and why). |

## Sequencing

```text
    metadata_migration  →  data path (D / E / B)  →  validate_migration  →  shadow_diff [+ replay]  →  cutover
```

Run the metadata migrator **first**: templates and pipelines should exist on
the destination before any data flows in, so backing indices are created with
the right settings/mappings on first write.

## Documentation

- [`docs/METADATA_MIGRATION.md`](../docs/METADATA_MIGRATION.md) — full runbook,
  sanitizer matrix, troubleshooting, and per-object report format.
- [`docs/SERVERLESS.md`](../docs/SERVERLESS.md) — recommended sequencing for
  Elastic Cloud Serverless destinations.
- [`docs/AUTOMATION.md`](../docs/AUTOMATION.md) — exit codes and `make` targets.
- [`docs/TOOLS.md`](../docs/TOOLS.md) — single-page index of every CLI in the repo.
