# Logstash: OpenSearch → Elastic Cloud

This directory contains a **Dockerfile** and **docker-compose** setup that runs Logstash with the `logstash-input-opensearch` plugin. Connection settings come from the **repository root `.env`** (same pattern as `validate_migration.py`). Do not commit `.env`.

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

## Large indices and resilience

- Prefer a **bounded query** in a custom pipeline for incremental runs (time range, etc.).
- Increase heap if needed by extending `docker-compose.yml` `environment` with `LS_JAVA_OPTS` or adding it to `.env` and referencing it in compose (ensure compose passes it through—today compose does not fix `LS_JAVA_OPTS`; add under `environment:` if you need it).

## Multiple indices

Run one container per index (change `LOGSTASH_SOURCE_INDEX` / `LOGSTASH_DEST_INDEX` per run) or duplicate services in `docker-compose.yml`. See repo [RUNBOOK.md](../RUNBOOK.md) for broader migration patterns.
