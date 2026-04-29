# Logstash: OpenSearch → Elastic Cloud

This directory contains a **Dockerfile** and **docker-compose** setup that runs Logstash with the `logstash-input-opensearch` plugin. Connection settings come from the **repository root `.env`** (same pattern as `validate_migration.py`). Do not commit `.env`.

**New users:** fill `.env` using [examples/env/logstash-cloud-id.env.example](../examples/env/logstash-cloud-id.env.example) or [logstash-api-key.env.example](../examples/env/logstash-api-key.env.example), or follow [docs/GETTING_STARTED.md](../docs/GETTING_STARTED.md).

## Local install (no Docker)

Use the **same** `pipeline/*.conf` files on a host-installed Logstash; set environment variables via systemd or shell. See [docs/PACKAGING.md](../docs/PACKAGING.md).

## Quick start (Docker Compose)

From **this directory** (`Logstash_input/`):

```bash
cd Logstash_input
cp ../.env.example ../.env
# Edit ../.env: SOURCE_OPENSEARCH_*, LOGSTASH_SOURCE_INDEX, LOGSTASH_DEST_INDEX,
#               ELASTIC_CLOUD_ID, ELASTIC_CLOUD_AUTH

docker compose up --build
```

Compose loads **`../.env`** automatically. Logstash substitutes variables in `pipeline/logstash.conf` at startup.

### Elastic Cloud with **API key** (instead of `cloud_auth`)

Use the `apikey` profile (mounts `pipeline/logstash_api_key.conf`):

```bash
docker compose --profile apikey up --build
```

Requires in `.env`: `DEST_ELASTIC_HOST`, `DEST_ELASTIC_API_KEY` (same as `validate_migration.py`), plus `SOURCE_OPENSEARCH_*` and `LOGSTASH_*` index names.

**API key format:** use the same Base64 value Elastic shows for an API key (or `id:api_key` if your Logstash version documents that form—see [Elasticsearch output plugin](https://www.elastic.co/guide/en/logstash/current/plugins-outputs-elasticsearch.html)).

### S3 input (read NDJSON from S3 → Elastic)

For partner data drops, archived OpenSearch dumps, or output produced by
[`s3_migration.s3_extract`](../s3_migration/s3_extract.py):

```bash
# Use examples/env/logstash-s3.env.example as a template; merge into ../.env.
docker compose --profile s3 up --build
```

Requires in `.env`: `S3_BUCKET`, `S3_PREFIX`, `AWS_REGION`, plus the same
`LOGSTASH_DEST_INDEX` and either `ELASTIC_CLOUD_ID` + `ELASTIC_CLOUD_AUTH` or
`DEST_ELASTIC_HOST` + `DEST_ELASTIC_API_KEY`. AWS credentials come from the
container task / instance role unless set explicitly.

**When to prefer the Python loader instead:** if the S3 prefix is bulk-format
NDJSON (alternating action / source lines, as produced by
`s3_migration.s3_extract`), use `python -m s3_migration.s3_bulk_load` — it
preserves `_id` and the action mapping exactly. This Logstash pipeline is best
for **source-only NDJSON** where you want Logstash's filter ecosystem.

### Proxy + SigV4 (OpenSearch IAM-only)

Point OpenSearch `hosts` at your HTTP proxy (repo [Proxy](../Proxy/README.md)):

```bash
# Example: proxy on the host machine (macOS/Windows Docker Desktop)
SOURCE_OPENSEARCH_HOST=http://host.docker.internal:9200
SOURCE_OPENSEARCH_USER=proxy_basic_auth_user
SOURCE_OPENSEARCH_PASSWORD=proxy_basic_auth_password
```

On Linux, use the host gateway IP or run Logstash on a network where the proxy hostname resolves.

## Environment variables (`.env`)

| Variable | Used by | Description |
|----------|---------|-------------|
| `SOURCE_OPENSEARCH_HOST` | input | OpenSearch URL, e.g. `https://search-....es.amazonaws.com:443` or proxy `http://...:9200` |
| `SOURCE_OPENSEARCH_USER` | input | Fine-grained access user or proxy basic-auth user |
| `SOURCE_OPENSEARCH_PASSWORD` | input | Matching password |
| `LOGSTASH_SOURCE_INDEX` | input | Source index to read |
| `LOGSTASH_DEST_INDEX` | output | Destination index in Elastic |
| `ELASTIC_CLOUD_ID` | output (default pipeline) | From Elastic Cloud deployment |
| `ELASTIC_CLOUD_AUTH` | output (default pipeline) | `elastic:password` or user with ingest rights |
| `DEST_ELASTIC_HOST` | output (apikey pipeline) | Elasticsearch URL |
| `DEST_ELASTIC_API_KEY` | output (apikey pipeline) | API key |

See also commented blocks in [.env.example](../.env.example).

## Custom query or pipeline

Default pipeline uses `match_all`. To use a different query or filters, copy `pipeline/logstash.conf` to a new file, edit, and run:

```bash
docker compose run --rm \
  -v "$PWD/pipeline/my-logstash.conf:/usr/share/logstash/pipeline/logstash.conf:ro" \
  logstash
```

## Manual image build (without Compose)

```bash
docker build -t oss-migration-logstash .
docker run --rm --env-file ../.env oss-migration-logstash
```

Ensure every `${...}` variable referenced in `pipeline/logstash.conf` is set in `--env-file`.

## Fine-grained access vs IAM-only

- **Fine-grained access user/password** on the OpenSearch domain: set `SOURCE_OPENSEARCH_*` to that user (see also `sample_logstash.conf`).
- **IAM-only domain:** run the [Proxy](../Proxy/README.md) and put its URL in `SOURCE_OPENSEARCH_HOST` with proxy credentials.

The older [sample_Dockerfile](sample_Dockerfile) / `sample_logstash.conf` flow still works; **prefer `Dockerfile` + `docker-compose.yml` here** for `.env`-driven runs.

## Ordering (FIFO-style per document id)

Elasticsearch does not guarantee global write order across shards. If **updates to the same `_id` must be applied in order**, use:

- **Single-threaded pipeline:** in `logstash.yml` (or pipeline settings), set `pipeline.workers: 1` and `pipeline.ordered: true` for this migration pipeline.
- **Do not** run multiple pipelines writing the **same** destination index concurrently if ordering matters.

For Kafka-mediated flows and **partition keys**, see [docs/KAFKA_MIGRATION.md](../docs/KAFKA_MIGRATION.md).

## Large indices, resilience, and backpressure

- Prefer a **bounded query** in a custom pipeline for incremental runs (time range, etc.).
- Increase heap if needed by extending `docker-compose.yml` `environment` with `LS_JAVA_OPTS` or adding it to `.env` and referencing it in compose (ensure compose passes it through—today compose does not fix `LS_JAVA_OPTS`; add under `environment:` if you need it).
- Configure the Elasticsearch output **retry** options per [Elastic documentation](https://www.elastic.co/guide/en/logstash/current/plugins-outputs-elasticsearch.html); consider enabling the **dead letter queue** in `logstash.yml` for poison documents.

### Handling backpressure and bulk rejections

When Elasticsearch rejects bulk requests (HTTP 429 or `EsRejectedExecutionException`), Logstash will retry automatically if the output plugin's retry settings are configured. Signs of backpressure:

- Logstash logs show `[retrying failed action with response code: 429]`
- Throughput drops or pipeline stalls
- Elasticsearch `GET _nodes/hot_threads` shows high threadpool queue depth

**Tuning steps (in order):**

1. **Reduce batch size.** In `docker-compose.yml` or `logstash.yml`, lower `pipeline.batch.size` (default 125). Smaller batches reduce memory pressure on Elasticsearch.
   ```yaml
   environment:
     - PIPELINE_BATCH_SIZE=50
   ```

2. **Slow the input.** Add a throttle or increase `pipeline.batch.delay` to give Elasticsearch time to catch up.

3. **Reduce replicas during migration.** On the destination index:
   ```json
   PUT /destination-index/_settings
   { "index": { "number_of_replicas": 0 } }
   ```
   Restore replicas after migration is complete.

4. **Increase `refresh_interval`.** A value of `-1` (disable) or `30s` during migration avoids frequent segment merges:
   ```json
   PUT /destination-index/_settings
   { "index": { "refresh_interval": "30s" } }
   ```

5. **Check JVM heap on Elasticsearch.** If heap usage is consistently >75%, consider scaling the cluster before continuing the migration.

6. **Enable the dead letter queue (DLQ)** in `logstash.yml` so poison documents (mapping errors, oversized docs) do not block the pipeline:
   ```yaml
   dead_letter_queue.enable: true
   dead_letter_queue.max_bytes: 1gb
   ```
   Review DLQ entries with the `dead_letter_queue` input plugin after migration.

## Multiple indices

Run one container per index (change `LOGSTASH_SOURCE_INDEX` / `LOGSTASH_DEST_INDEX` per run) or duplicate services in `docker-compose.yml`. See repo [RUNBOOK.md](../RUNBOOK.md) for broader migration patterns.
