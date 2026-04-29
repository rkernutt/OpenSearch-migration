# Metadata migration (templates, component templates, ingest pipelines)

Runbook for [`metadata_migration`](../metadata_migration/), the toolkit's
answer to upstream's `MetadataMigration` module + type-mapping sanitization
transformer. Pairs with any of the data paths (remote reindex, Logstash, S3
staging, RFS): run **metadata first**, then load the documents.

## When to use it

- The destination is **Elastic Cloud Serverless** and you need shard /
  replica / merge / translog settings stripped before templates are applied.
- The source is **Elasticsearch ≤ 6.x** and you need multi-type mappings
  flattened into typeless 7+/Serverless mappings (the closest equivalent of
  upstream's `jsonTypeMappingsSanitizationTransformer`).
- You're moving cluster-level objects between clusters that are otherwise
  unreachable from each other (hand-off via JSON files using the standalone
  sanitizer CLI).

## What it migrates

| Kind | Source endpoint | Destination endpoint | Sanitized |
|------|-----------------|----------------------|-----------|
| Legacy index templates | `GET /_template` | `PUT /_template/<name>` | settings + mappings |
| Composable index templates | `GET /_index_template` | `PUT /_index_template/<name>` | inner `template` block |
| Component templates | `GET /_component_template` | `PUT /_component_template/<name>` | inner `template` block |
| Ingest pipelines | `GET /_ingest/pipeline` | `PUT /_ingest/pipeline/<name>` | OpenSearch-only meta tags stripped |

Index aliases are *not* migrated by this tool — they're applied per-index by
whichever data path you use. ILM policies, ISM policies, anomaly detectors,
notifications, replication, and other OpenSearch-only objects are
**warn-and-skip**: translate them by hand, or copy directly into Elastic
console.

## What gets sanitized

### Index settings

For `--target-type ELASTICSEARCH_SERVERLESS` the following prefixes are
removed (mirroring upstream RFS):

```
index.number_of_shards         index.translog.*
index.number_of_replicas       index.merge.*
index.auto_expand_replicas     index.store.*
index.routing.allocation.*     index.unassigned.*
index.codec                    index.allocation.*
index.shard.check_on_startup   index.search.idle.after
```

OpenSearch-only settings (`index.knn`, `index.replication.*`,
`index.opendistro*`, `index.opensearch*`) are removed regardless of target.

`index.refresh_interval`, `index.max_result_window`, analysis settings, and
mapping definitions are **preserved**.

### Mappings

Multi-version translations (closest equivalent of upstream's type-mapping
sanitization transformer):

| Source shape | Action |
|--------------|--------|
| Multiple types per index (ES 5.x) | Flatten into one typeless `properties` map; first non-default type wins, conflicts reported. |
| `_default_` template type | Dropped with a note. |
| Top-level `_all`, `_timestamp`, `_ttl` | Dropped. |
| Field `type: string` | → `text` with a `keyword` multi-field (or `keyword` if `index: not_analyzed`). |
| Field `index: "not_analyzed" / "analyzed"` | Removed (now bool-only). |
| Field `index: "no"` | → `index: false`. |
| Field `include_in_all`, `boost`, `fielddata_frequency_filter`, `norms.enabled` | Dropped. |

The sanitizer recurses into nested `properties` and `fields`.

## CLI quick start

### Migrator

```bash
python -m metadata_migration.migrator \
  --source-host "$SOURCE_OPENSEARCH_HOST" \
  --source-user "$SOURCE_OPENSEARCH_USER" \
  --source-password "$SOURCE_OPENSEARCH_PASSWORD" \
  --dest-host "$DEST_ELASTIC_HOST" \
  --dest-api-key "$DEST_ELASTIC_API_KEY" \
  --target-type ELASTICSEARCH_SERVERLESS \
  --include templates,index_templates,component_templates,ingest_pipelines \
  --name 'logs-*' --name 'metrics-*' \
  --report-dir ./.metadata-reports \
  --strict-exit-codes --log-format json
```

SigV4 auth: omit `--source-user/--source-password` and pass `--source-region`
(or set `AWS_REGION`) — the AWS provider chain takes over. Add `--via-proxy`
for a cosmetic audit-log tag when going through the in-repo SigV4 proxy.

`--dry-run` reads + sanitizes everything but does not PUT, and prints the
summary so you can review what would change.

`--report-dir` writes one JSON report per object (`{kind}__{name}.json`) with
both the sanitized body and the change log. Useful for code-review or audit
trails before a Serverless cutover.

### Standalone sanitizer

When you need to sanitize a JSON file (e.g. before pre-creating a destination
index for the S3 staging path) without talking to a cluster:

```bash
# Full index body (settings + mappings + aliases)
cat my-index-body.json \
  | python -m metadata_migration.sanitizer \
      --mode index-body \
      --target-type ELASTICSEARCH_SERVERLESS \
      --report ./.sanitize-report.json \
  > sanitized.json

# Just a mapping
python -m metadata_migration.sanitizer \
  --input mapping.json --output mapping.serverless.json \
  --mode mapping --target-type ELASTICSEARCH_SERVERLESS

# Strict mode: exit 1 if anything had to be changed (CI-friendly)
python -m metadata_migration.sanitizer --input x.json --strict
```

## Recommended sequencing

```
preflight  →  metadata migration  →  data path (any of 5 options)  →  validate
```

Why metadata first: templates and ingest pipelines often need to be in place
*before* indices are created so that mappings, settings, and on-ingest
processors apply to the migrated documents.

## Filters

| Flag | Default | Purpose |
|------|---------|---------|
| `--include` | `templates,index_templates,component_templates,ingest_pipelines` | Object kinds to migrate. |
| `--name PATTERN` (repeatable) | `*` | Glob include filter (e.g. `--name 'logs-*'`). |
| `--exclude PATTERN` (repeatable) | none | Glob exclude filter. |
| `--keep-system-objects` | off | Include objects starting with `.` or `_` (Kibana / built-ins). |
| `--overwrite` | off | PUT through even if the destination already has the object. |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Exit **3** "failed to read templates from source" | Source 401/403, network blip, or wrong auth mode. | Check fine-grained access / IAM role; rerun. |
| Exit **4** with a single failed object whose error mentions `mapper_parsing_exception` | Mapping conflict that the sanitizer couldn't auto-resolve. | Inspect `--report-dir`'s entry for the object; pre-create the destination index manually with a reconciled mapping. |
| `type_conflicts` reported for a multi-type source | Two types defined the same field name with different settings. | The sanitizer kept the first writer; either rename one field on the source, or pre-create the destination with the desired mapping. |
| Index template PUT succeeds but later index creation fails on Serverless | A setting allowed in the template format but disallowed at index time slipped through. | Run `metadata_migration.sanitizer --mode index-body` against the offending template's `template` block to identify; open an issue with the JSON. |

## See also

- [AUTOMATION.md](AUTOMATION.md) — exit codes for the migrator and sanitizer.
- [S3_MIGRATION.md](S3_MIGRATION.md) — pair this with Path D (S3 staging).
- [RFS.md](RFS.md) — the wrapped RFS path applies its own settings
  sanitization via `--target-type`; you only need this tool for templates and
  pipelines if you use RFS.
- [SERVERLESS.md](SERVERLESS.md) — Serverless destination notes.
