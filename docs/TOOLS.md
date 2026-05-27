# Tools index

A single-page reference to every CLI tool in this repository, the `migrate`
umbrella subcommand it maps to, the matching `make` target, and where to
look for full documentation.

For deeper guides, follow the **Docs** column. For first-time setup, see
[GETTING_STARTED.md](GETTING_STARTED.md). For runbooks, see
[../RUNBOOK.md](../RUNBOOK.md).

## Umbrella entry point

[`migrate.py`](../migrate.py) wraps every script below. After
`pip install -e .` it's available as the `migrate` console script.

```bash
migrate --help                      # list every subcommand
migrate <subcommand> --help         # full flag list for that subcommand
migrate --version                   # print __version__
```

## Planning (run before the data path)

| Subcommand | `make` target | Module | What it does | Docs |
|-----------|----------------|--------|--------------|------|
| `migrate compat-check` | `make compat-check` | [`compat_check.py`](../compat_check.py) | Pre-flight compatibility report: source/destination versions, Lucene window, k-NN indices, OpenSearch-only codecs, Serverless-forbidden settings, ES 5/6 mapping artefacts. Recommends which path (B / D / E) is safe. | [COMPAT_CHECK.md](COMPAT_CHECK.md) |

## Data paths (load data)

| Subcommand | `make` target | Module | What it does | Docs |
|-----------|----------------|--------|--------------|------|
| `migrate s3-extract` | `make s3-extract` | [`s3_migration/s3_extract.py`](../s3_migration/s3_extract.py) | Path D extract: sliced-scroll OpenSearch indices into gzipped NDJSON parts in S3, with manifest + checkpoint. | [S3_MIGRATION.md](S3_MIGRATION.md) |
| `migrate s3-load` | `make s3-load` | [`s3_migration/s3_bulk_load.py`](../s3_migration/s3_bulk_load.py) | Path D load: stream gzipped NDJSON parts from S3 and POST them to Elasticsearch's `_bulk` API. | [S3_MIGRATION.md](S3_MIGRATION.md) |
| `migrate rfs` | `make rfs` | [`s3_migration/rfs_runner.py`](../s3_migration/rfs_runner.py) | Path E: thin wrapper around the upstream OpenSearch Migrations RFS container; auto-runs `validate_migration.py` afterwards. | [RFS.md](RFS.md) |
| `migrate reindex-gen` | n/a | [`multi_index_reindex.py`](../multi_index_reindex.py) | Path A helper: build remote-reindex POST bodies for one or more indices. | [../Remote_Reindex/README.md](../Remote_Reindex/README.md) |

There is **no** umbrella subcommand for **Logstash** (Path B) or **Kafka**
(Path C); those run as their own services. See
[`../Logstash_input/README.md`](../Logstash_input/README.md) and
[KAFKA_MIGRATION.md](KAFKA_MIGRATION.md).

## Metadata + sanitization (run before the data path)

| Subcommand | `make` target | Module | What it does | Docs |
|-----------|----------------|--------|--------------|------|
| `migrate metadata` | `make metadata` | [`metadata_migration/migrator.py`](../metadata_migration/migrator.py) | Copy index templates, component templates, composable index templates, and ingest pipelines from source to dest with optional Serverless settings sanitization and ES 5/6 multi-type mapping flatten. | [METADATA_MIGRATION.md](METADATA_MIGRATION.md) |
| `migrate sanitize` | `make sanitize` | [`metadata_migration/sanitizer.py`](../metadata_migration/sanitizer.py) | Sanitize a standalone index settings/mapping JSON file (e.g. before pre-creating an index by hand). | [METADATA_MIGRATION.md](METADATA_MIGRATION.md) |

## Validation + cutover gates (run after the data path)

| Subcommand | `make` target | Module | What it does | Docs |
|-----------|----------------|--------|--------------|------|
| `migrate preflight` | `make preflight` | [`preflight.py`](../preflight.py) | Pre-load: source/dest reachability + auth + count parity sanity checks. | [AUTOMATION.md](AUTOMATION.md) |
| `migrate validate` | `make validate` | [`validate_migration.py`](../validate_migration.py) | Post-load: count comparison and sampled `_mget` reconciliation across one or many indices. | [../README.md](../README.md), [TESTING.md](TESTING.md) |
| `migrate poll-task` | `make poll-task` | [`poll_reindex_task.py`](../poll_reindex_task.py) | Poll a long-running OpenSearch `_reindex` task to completion (Path A). | [../RUNBOOK.md](../RUNBOOK.md) |
| `migrate shadow-diff` | `make shadow-diff` | [`shadow_diff.py`](../shadow_diff.py) | Cutover gate: replay curated queries against source + dest, comparing counts, top-K hit IDs (Jaccard), and per-hit canonical-JSON SHA-256s. Strict exit codes. | [SHADOW_DIFF.md](SHADOW_DIFF.md) |
| `migrate replay` | `make replay` | [`replay/replayer.py`](../replay/replayer.py) | Path F: replay captured NDJSON traffic against the destination, comparing status, size, and canonical-JSON hash. Sampled cutover validation. | [CAPTURE_REPLAY.md](CAPTURE_REPLAY.md) |

## Long-running services

These do not run via `migrate`; they start their own processes.

| Component | Module / file | What it does | Docs |
|-----------|---------------|--------------|------|
| SigV4 reverse proxy | [`Proxy/app.py`](../Proxy/app.py) | HTTP → SigV4 forwarder for OpenSearch VPC endpoints (Logstash, Elastic Cloud remote reindex). | [../Proxy/README.md](../Proxy/README.md) |
| Proxy capture engine | [`Proxy/capture.py`](../Proxy/capture.py) | Bounded-queue background writer that tees proxied traffic to NDJSON (local or S3). Enabled with `PROXY_CAPTURE_MODE=local|s3`. | [CAPTURE_REPLAY.md](CAPTURE_REPLAY.md) |
| Logstash pipelines | [`../Logstash_input/`](../Logstash_input/) | Docker Compose stacks for OpenSearch → Elastic, S3 → Elastic, and proxy → Elastic. | [../Logstash_input/README.md](../Logstash_input/README.md) |

## Strict exit codes

Every CLI in the table above accepts `--strict-exit-codes` and follows the
same convention:

| Code | Meaning |
|------|---------|
| `0` | Success. |
| `2` | Configuration error (bad flag, missing host, unreadable file, …). |
| `3` | Transport / auth / TLS failure on source or destination. |
| `4` | Domain failure: `validate` and `shadow-diff` use this for parity drift; `s3-load` and `metadata` use it for per-document or per-object failures; `replay` uses it for response drift; `compat-check` uses it for compatibility warnings. |
| `1` | Generic failure when `--strict-exit-codes` was not requested. |

Full table per script: [AUTOMATION.md](AUTOMATION.md).

## Environment variables

Each tool reuses the shared variables documented in [`../.env.example`](../.env.example):

- `SOURCE_OPENSEARCH_HOST`, `SOURCE_OPENSEARCH_USER`, `SOURCE_OPENSEARCH_PASSWORD`, `AWS_REGION`
- `DEST_ELASTIC_HOST`, `DEST_ELASTIC_API_KEY` (or `DEST_ELASTIC_USER`/`DEST_ELASTIC_PASSWORD`)

Tool-specific variables (capture mode, Logstash, Kafka, etc.) are listed in:

- [`../.env.example`](../.env.example) — the full reference.
- [`../examples/env/`](../examples/env/) — focused templates per tool.
- [Proxy capture mode](../Proxy/README.md#capture-mode-optional) — `PROXY_CAPTURE_*`.

## Where to go next

- **First-time setup:** [GETTING_STARTED.md](GETTING_STARTED.md).
- **Choosing a path:** [../README.md](../README.md) — the path table.
- **Network topology:** [NETWORK_TOPOLOGY.md](NETWORK_TOPOLOGY.md) — which path works under your VPC / PrivateLink layout.
- **Versions:** [VERSION_MATRIX.md](VERSION_MATRIX.md) — Lucene window, k-NN, codecs.
- **Architecture diagrams:** [ARCHITECTURE.md](ARCHITECTURE.md).
- **Runbooks:** [../RUNBOOK.md](../RUNBOOK.md) — option-by-option procedures.
- **Production checklist:** [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md).
- **Troubleshooting:** [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — common errors and fixes.
