# Getting started (first-time users)

This guide assumes you are **new to this repository**. You want to move data from **OpenSearch** (on-premises or Amazon OpenSearch Service) to **Elasticsearch** (often Elastic Cloud) and verify the result.

## What this project gives you

- **Patterns and scripts**, not a single “Migrate” button: remote **reindex** from Elastic, **Logstash** streaming, optional **Kafka** design notes, a **SigV4 proxy** for private OpenSearch, and **validation** CLI.
- **Configuration** is mostly **environment variables** in a repo-root **`.env`** file (never commit it). Example templates live in [examples/env](../examples/env/) and the full reference is [`.env.example`](../.env.example).

For deeper operations (ordering, retries, large indices, semantic fields), use [RUNBOOK.md](../RUNBOOK.md).

## Prerequisites

- **Python 3.9+** if you use the validation scripts (`validate_migration.py`, `poll_reindex_task.py`).
- **Network:** your machine or runner can reach **OpenSearch** and **Elastic** (or you will use a **jump host** / **proxy**—see [Proxy/README.md](../Proxy/README.md)).
- **Credentials:** OpenSearch (IAM/SigV4 from AWS credentials, or master user/password). Elastic (**API key** or user/password). Amazon OpenSearch needs **`AWS_REGION`** when using SigV4.
- **Docker** (optional) if you use the **Logstash** path in [Logstash_input](../Logstash_input/).

## Step 1: Clone the repository

```bash
git clone <your-fork-or-upstream-url>
cd OpenSearch-migration
```

## Step 2: Create your `.env` file

1. Choose a template from [examples/env](../examples/env/) (see that folder’s [README](../examples/env/README.md)), **or** copy the full template:

   ```bash
   cp .env.example .env
   ```

2. For a **minimal** validation-only setup:

   ```bash
   cp examples/env/validation.env.example .env
   ```

3. Edit **`.env`** in the repo root and replace every placeholder:

   | Variable | Meaning |
   |----------|--------|
   | `SOURCE_OPENSEARCH_HOST` | OpenSearch HTTPS endpoint (no trailing path). |
   | `AWS_REGION` | AWS region of the OpenSearch domain (for SigV4). |
   | `SOURCE_OPENSEARCH_USER` / `SOURCE_OPENSEARCH_PASSWORD` | Optional; set if you use basic auth instead of SigV4. |
   | `DEST_ELASTIC_HOST` | Elasticsearch HTTPS endpoint (Elastic Cloud or self-managed). |
   | `DEST_ELASTIC_API_KEY` | Elastic API key (recommended). Or use `DEST_ELASTIC_USER` + `DEST_ELASTIC_PASSWORD`. |
   | `LOGSTASH_SOURCE_INDEX` / `LOGSTASH_DEST_INDEX` | Only for Logstash: source and destination **index names**. |
   | `ELASTIC_CLOUD_ID` / `ELASTIC_CLOUD_AUTH` | Only for default Logstash Compose (`elastic:password`). |
   | `DEST_ELASTIC_*` | Same host/API key if you use **`docker compose --profile apikey`**. |

**Security:** `.env` is listed in `.gitignore`. Do not paste secrets into tickets or commit them. See [SECURITY.md](../SECURITY.md).

## Step 3: Install Python dependencies

```bash
pip install -r requirements.txt
```

This installs `requests`, AWS signing libraries, and **`python-dotenv`** so the scripts automatically load **`.env`** from the repo root when you run them.

## Step 4: Choose how you will migrate

Pick **one primary path** (you can use validation after any path):

| Path | Best when | Where to learn more |
|------|-----------|---------------------|
| **A. Remote reindex** | Elastic **Cloud Hosted**, OpenSearch reachable from Elastic; one-off or large batch | [Remote_Reindex/README.md](../Remote_Reindex/README.md), [RUNBOOK.md](../RUNBOOK.md) Option A |
| **B. Logstash** | Streaming, or Elastic **Serverless** destination, or you prefer a pipeline | [Logstash_input/README.md](../Logstash_input/README.md), [RUNBOOK.md](../RUNBOOK.md) Option B |
| **C. Kafka** | You need a durable buffer / replay; you operate Kafka | [KAFKA_MIGRATION.md](KAFKA_MIGRATION.md) |

Remote reindex is configured in **Kibana Dev Tools** on **Elastic** (JSON bodies under `Remote_Reindex/`). Those requests use credentials you put **inside the Dev Tools JSON** (`source.remote` user/password), not necessarily your `.env`. Use **`.env`** for the **Python validation** step.

## Step 5: Run the data move (brief pointers)

- **Remote reindex:** Allowlist OpenSearch on Elastic, then paste and run e.g. [Elastic_DEVTOOLS_reindex.json](../Remote_Reindex/Elastic_DEVTOOLS_reindex.json) (edit host and index names). For large indices, use the `_large` variant and [poll_reindex_task.py](../poll_reindex_task.py).
- **Logstash:** Merge [examples/env/logstash-cloud-id.env.example](../examples/env/logstash-cloud-id.env.example) or [logstash-api-key.env.example](../examples/env/logstash-api-key.env.example) into `.env`, then:

  ```bash
  cd Logstash_input
  docker compose up --build
  # or: docker compose --profile apikey up --build
  ```

## Step 6: Validate source vs destination

Index names are passed on the **command line** (they are not required in `.env` for this script).

Single index:

```bash
python validate_migration.py \
  --source-index YOUR_OPENSEARCH_INDEX \
  --dest-index YOUR_ELASTIC_INDEX
```

The script reads `SOURCE_OPENSEARCH_HOST`, `DEST_ELASTIC_HOST`, auth, and `AWS_REGION` from your **`.env`** (or you can pass `--source-host`, `--dest-host`, etc.—run `python validate_migration.py --help`).

Optional checks: `--sample-size 25` to verify random IDs exist on Elastic via `_mget`; batch mode `--indices` or `--indices-file`. See [README.md](../README.md) and [docs/TESTING.md](TESTING.md).

## Step 7: Poll an async reindex task (if needed)

If `POST _reindex` returned a task id:

```bash
python poll_reindex_task.py --task-id "paste-task-id-here"
```

Uses **`DEST_ELASTIC_*`** from `.env`.

## Checklist before production

- [ ] `.env` not committed; API keys scoped to least privilege ([SECURITY.md](../SECURITY.md)).
- [ ] Destination index settings tuned for bulk load if large ([Elastic_destination_index_settings.json](../Remote_Reindex/Elastic_destination_index_settings.json)).
- [ ] Cutover and rollback understood ([RUNBOOK.md](../RUNBOOK.md)).

## Where to go next

- [RUNBOOK.md](../RUNBOOK.md) — full procedure, versioning, ordering, throughput.
- [docs/SERVERLESS.md](SERVERLESS.md) — Elastic Serverless and OpenSearch Serverless caveats.
- [docs/SEMANTIC_MIGRATION.md](SEMANTIC_MIGRATION.md) — vector / `semantic_text` follow-ups.

If something fails (401/403, TLS, connectivity), see [docs/TLS_AND_CREDENTIAL_LIFECYCLE.md](TLS_AND_CREDENTIAL_LIFECYCLE.md) and your cloud provider’s networking docs.
