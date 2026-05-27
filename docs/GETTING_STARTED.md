# Getting started (first-time users)

This guide assumes you are **new to this repository**. You want to move data from **OpenSearch** (on-premises or Amazon OpenSearch Service) to **Elasticsearch** (often Elastic Cloud) and verify the result.

**Environmental factors:** Your clusters, network, security policies, and data are unique. You may see timeouts, auth errors, mapping rejections, or slow throughput until you tune paths and resources for **your** environment. Treat the first run as a **pilot**; use [preflight.py](../preflight.py) and [validate_migration.py](../validate_migration.py) before relying on a full migration. The main [README](../README.md) has a longer **Caveats and scope** section.

## What this project gives you

- **Six data paths**, plus cutover gates and metadata sanitization:
  - **Path A — Remote reindex** from Elastic (Hosted only).
  - **Path B — Logstash** (streaming; works to Hosted and Serverless).
  - **Path C — Kafka** (architecture-only design notes).
  - **Path D — S3 staging** (extract → S3 → bulk load; air-gap-friendly).
  - **Path E — Reindex-from-Snapshot** (wraps upstream RFS image; Lucene-aware).
  - **Path F — Capture & replay** (cutover validation, not a primary load path).
- **Cutover gates:** [`shadow_diff.py`](../shadow_diff.py) (curated query parity) and the replay path (sampled real-traffic parity). Both exit non-zero on drift.
- **Metadata migration:** [`metadata_migration`](../metadata_migration/) copies templates / component templates / ingest pipelines with optional Serverless settings sanitization and ES 5/6 multi-type mapping flatten.
- **Validation, preflight, polling, multi-index:** [`validate_migration.py`](../validate_migration.py), [`preflight.py`](../preflight.py), [`poll_reindex_task.py`](../poll_reindex_task.py), [`multi_index_reindex.py`](../multi_index_reindex.py).
- **Single binary, all paths:** [`migrate.py`](../migrate.py) — `migrate preflight`, `migrate s3-load`, `migrate metadata`, `migrate shadow-diff`, `migrate replay`, etc. Run `migrate --help` to see every subcommand. `pip install -e .` exposes it as the `migrate` console script.
- **Configuration** is mostly **environment variables** in a repo-root **`.env`** file (never commit it). Example templates live in [examples/env](../examples/env/) and the full reference is [`.env.example`](../.env.example).

For deeper operations (ordering, retries, large indices, semantic fields), use [RUNBOOK.md](../RUNBOOK.md). For a single-page index of every CLI tool, see [docs/TOOLS.md](TOOLS.md).

## Prerequisites

- **Python 3.10+** if you use the validation scripts (`validate_migration.py`, `poll_reindex_task.py`). CI runs 3.10, 3.11 and 3.12.
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

Optional: run unit tests with **`make test`** (see [Makefile](../Makefile)) or `python -m pytest -q`. For CI-style exit codes and `make` targets, read [docs/AUTOMATION.md](AUTOMATION.md).

## Step 4: Choose how you will migrate

Pick **one primary path** (you can use validation and the cutover gates after any path):

| Path | Best when | Where to learn more |
|------|-----------|---------------------|
| **A. Remote reindex** | Elastic **Cloud Hosted**, OpenSearch reachable from Elastic; one-off or large batch | [Remote_Reindex/README.md](../Remote_Reindex/README.md), [RUNBOOK.md](../RUNBOOK.md) Option A |
| **B. Logstash** | Streaming, or Elastic **Serverless** destination, or you prefer a pipeline | [Logstash_input/README.md](../Logstash_input/README.md), [RUNBOOK.md](../RUNBOOK.md) Option B |
| **C. Kafka** | You need a durable buffer / replay; you operate Kafka | [KAFKA_MIGRATION.md](KAFKA_MIGRATION.md) |
| **D. S3 staging** | VPC-only source, Serverless destination, or air-gapped operators | [S3_MIGRATION.md](S3_MIGRATION.md), [RUNBOOK.md](../RUNBOOK.md) Option D |
| **E. RFS (wrapped)** | You already snapshot to S3; multi-TB; Lucene-aware | [RFS.md](RFS.md), [RUNBOOK.md](../RUNBOOK.md) Option E |

Two extra gates that pair with **any** path:

| Gate | Purpose | Docs |
|------|---------|------|
| **Metadata first** (run before the data path) | Copy templates / component templates / ingest pipelines with sanitization | [METADATA_MIGRATION.md](METADATA_MIGRATION.md) |
| **shadow_diff** (run before cutover) | Curated query parity check; non-zero exit on drift | [SHADOW_DIFF.md](SHADOW_DIFF.md) |
| **Capture & replay** (Path F; run before cutover) | Sampled real-traffic parity; non-zero on drift | [CAPTURE_REPLAY.md](CAPTURE_REPLAY.md) |

Remote reindex is configured in **Kibana Dev Tools** on **Elastic** (JSON bodies under `Remote_Reindex/`). Those requests use credentials you put **inside the Dev Tools JSON** (`source.remote` user/password), not necessarily your `.env`. Use **`.env`** for the **Python validation** step.

## Step 5: Compatibility + connectivity preflight (recommended)

Run **two** checks before a long data move. They take seconds and catch
the most common cross-cluster surprises:

1. **Compatibility scan** — Lucene gap, k-NN indices, OS-only codecs,
   ES 5/6 mapping artefacts, Serverless-forbidden settings:

   ```bash
   migrate compat-check --strict-exit-codes
   ```

   Exit `0` means any path works; exit `4` means open the per-index
   report and prefer Path D / B over RFS. Full guide:
   [docs/COMPAT_CHECK.md](COMPAT_CHECK.md).

2. **Connectivity / auth / count parity:**

   ```bash
   python preflight.py --strict-exit-codes --source-index YOUR_OPENSEARCH_INDEX --dest-index YOUR_ELASTIC_INDEX
   ```

Add `--check-counts` to `preflight` to require matching `_count`. See
[docs/AUTOMATION.md](AUTOMATION.md) and `make preflight ARGS='...'`,
`make compat-check ARGS='...'` in the [Makefile](../Makefile).

## Step 6: Run the data move (brief pointers)

- **Remote reindex:** Allowlist OpenSearch on Elastic, then paste and run e.g. [Elastic_DEVTOOLS_reindex.json](../Remote_Reindex/Elastic_DEVTOOLS_reindex.json) (edit host and index names). For large indices, use the `_large` variant and [poll_reindex_task.py](../poll_reindex_task.py).
- **Logstash:** Merge [examples/env/logstash-cloud-id.env.example](../examples/env/logstash-cloud-id.env.example) or [logstash-api-key.env.example](../examples/env/logstash-api-key.env.example) into `.env`, then:

  ```bash
  cd Logstash_input
  docker compose up --build
  # or: docker compose --profile apikey up --build
  ```

## Step 7: Validate source vs destination

Index names are passed on the **command line** (they are not required in `.env` for this script).

Single index:

```bash
python validate_migration.py \
  --strict-exit-codes \
  --source-index YOUR_OPENSEARCH_INDEX \
  --dest-index YOUR_ELASTIC_INDEX
```

The script reads `SOURCE_OPENSEARCH_HOST`, `DEST_ELASTIC_HOST`, auth, and `AWS_REGION` from your **`.env`** (or you can pass `--source-host`, `--dest-host`, etc.—run `python validate_migration.py --help`).

Optional checks: `--sample-size 25` to verify random IDs exist on Elastic via `_mget`; batch mode `--indices` or `--indices-file`. See [README.md](../README.md) and [docs/TESTING.md](TESTING.md).

## Step 8: Poll an async reindex task (if needed)

If `POST _reindex` returned a task id:

```bash
python poll_reindex_task.py --strict-exit-codes --task-id "paste-task-id-here"
```

Uses **`DEST_ELASTIC_*`** from `.env`.

## Checklist before production

Use the full list in [docs/PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md). The minimum:

- [ ] `migrate compat-check --strict-exit-codes` exits 0 (or the warnings are reviewed) — [docs/COMPAT_CHECK.md](COMPAT_CHECK.md).
- [ ] `.env` not committed; API keys scoped to least privilege ([SECURITY.md](../SECURITY.md)).
- [ ] Destination index settings tuned for bulk load if large ([Elastic_destination_index_settings.json](../Remote_Reindex/Elastic_destination_index_settings.json)).
- [ ] Cutover and rollback understood ([RUNBOOK.md](../RUNBOOK.md)).

## Where to go next

- [RUNBOOK.md](../RUNBOOK.md) — full procedure for every option (A–F), versioning, ordering, throughput.
- [docs/TOOLS.md](TOOLS.md) — single-page index of every CLI tool with one-line descriptions.
- [docs/NETWORK_TOPOLOGY.md](NETWORK_TOPOLOGY.md) — which path works under public / VPC / PrivateLink / air-gapped layouts.
- [docs/VERSION_MATRIX.md](VERSION_MATRIX.md) — Lucene window and per-feature path matrix.
- [docs/PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md) — full sign-off list for production cutover.
- [docs/SERVERLESS.md](SERVERLESS.md) — Elastic Serverless and OpenSearch Serverless caveats; recommended path is metadata + S3/RFS + shadow_diff.
- [docs/METADATA_MIGRATION.md](METADATA_MIGRATION.md) — templates, pipelines, settings/mapping sanitization.
- [docs/SHADOW_DIFF.md](SHADOW_DIFF.md) — query-parity cutover gate.
- [docs/CAPTURE_REPLAY.md](CAPTURE_REPLAY.md) — Path F (proxy-tee + replayer).
- [docs/SEMANTIC_MIGRATION.md](SEMANTIC_MIGRATION.md) — vector / `semantic_text` follow-ups.

If something fails (401/403, TLS, connectivity, mapping, codec, vector), see [docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md) and [docs/TLS_AND_CREDENTIAL_LIFECYCLE.md](TLS_AND_CREDENTIAL_LIFECYCLE.md).
