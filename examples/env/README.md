# Example environment files

These files are **templates only**. Copy the one that matches how you work into the **repository root** as **`.env`** (or merge lines into an existing `.env`).

| File | Use when |
|------|----------|
| [validation.env.example](validation.env.example) | You run `validate_migration.py` and/or `poll_reindex_task.py` only (compare counts, sample docs). |
| [logstash-cloud-id.env.example](logstash-cloud-id.env.example) | You use **Docker Compose** Logstash with **Elastic Cloud ID** + **cloud_auth** (`elastic:password`). |
| [logstash-api-key.env.example](logstash-api-key.env.example) | You use **`docker compose --profile apikey`** Logstash with **Elasticsearch URL** + **API key**. |
| [logstash-s3.env.example](logstash-s3.env.example) | You use **`docker compose --profile s3`** Logstash to read NDJSON parts from S3 (e.g. produced by `s3_extract.py`) and write to Elastic Cloud. |
| [metadata.env.example](metadata.env.example) | You run `migrate metadata` / `migrate sanitize` to copy templates, component templates, and ingest pipelines (with optional Serverless settings sanitization). |
| [shadow-diff.env.example](shadow-diff.env.example) | You run `migrate shadow-diff` (a.k.a. `shadow_diff.py`) as a query-parity cutover gate before flipping production traffic. |
| [proxy-capture.env.example](proxy-capture.env.example) | You run the SigV4 proxy with `PROXY_CAPTURE_MODE=local|s3` to tee traffic to NDJSON for later replay. |
| [replay.env.example](replay.env.example) | You run `migrate replay` to replay captured NDJSON traffic against the destination (sampled cutover validation, Path F). |

The **full** reference (Proxy, integration tests, optional toggles) is always in the repo root [`.env.example`](../../.env.example).

## Merge into one `.env`

Docker Compose loads **`../.env`** from `Logstash_input/`. Python tools load **repo-root** `.env` via `python-dotenv`. Keep **one** `.env` at the repo root:

```bash
cd /path/to/OpenSearch-migration
cp examples/env/validation.env.example .env
# Edit .env, then add Logstash lines from logstash-*.env.example if needed
```

**Do not commit `.env`.** It is gitignored.

## Where values come from

- **OpenSearch URL:** AWS console → OpenSearch domain → endpoint (HTTPS), e.g. `https://search-*....amazonaws.com`.
- **Elastic Cloud URL:** Deployment → copy Elasticsearch endpoint.
- **Elastic API key:** Kibana / Deployment → **API keys** → create with `cluster` / `ingest` (and read) privileges as needed.
- **Elastic Cloud ID & cloud_auth:** Deployment overview in Elastic Cloud console.

See [docs/GETTING_STARTED.md](../../docs/GETTING_STARTED.md) for a full walkthrough. After filling `.env`, you can run [preflight.py](../../preflight.py) (`python preflight.py --strict-exit-codes`) to verify reachability before a long migration.
