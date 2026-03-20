# Runbook: Amazon OpenSearch Service → Elastic Cloud migration

Use this runbook for a single migration or a multi-index migration.

Before production cutover, run a **smoke test** and optional **`pytest`** checks—see [docs/TESTING.md](docs/TESTING.md).

For a **copy-paste org template** (RACI, links, checklists), see [docs/RUNBOOK_TEMPLATE.md](docs/RUNBOOK_TEMPLATE.md).

## Prerequisites

- **Elastic Cloud Hosted** deployment (remote reindex is not supported on Serverless).
- OpenSearch domain reachable from Elastic Cloud (public endpoint or proxy). If the domain is VPC-only, use a public endpoint or the [Proxy](Proxy/README.md) in AWS that signs requests with SigV4.
- Credentials:
  - **Remote reindex:** OpenSearch username/password (fine-grained access), or ensure the domain accepts requests from Elastic’s IPs with appropriate auth.
  - **Logstash:** OpenSearch user/password, or IAM + proxy (see [Logstash_input/README.md](Logstash_input/README.md)).
  - **Elastic:** API key or username/password for the Elastic deployment.

## Option A: Remote reindex (recommended for one-off or batch)

1. **Configure allowlist on Elastic Cloud**  
   Edit deployment → User settings. Add:
   ```yaml
   reindex.remote.whitelist: ["search-your-opensearch-domain.region.es.amazonaws.com:443"]
   ```
   Save and wait for the deployment to apply.

2. **(Optional) Create destination index with performance settings**  
   For large indices, create the destination index with `refresh_interval: -1` and `number_of_replicas: 0` using [Remote_Reindex/Elastic_destination_index_settings.json](Remote_Reindex/Elastic_destination_index_settings.json). After reindex, restore desired settings.

3. **Run reindex**  
   In Kibana Dev Tools on the **Elastic** deployment:
   - Single index: use [Remote_Reindex/Elastic_DEVTOOLS_reindex.json](Remote_Reindex/Elastic_DEVTOOLS_reindex.json) (replace host, credentials, index names).
   - Large index: use [Remote_Reindex/Elastic_DEVTOOLS_reindex_large.json](Remote_Reindex/Elastic_DEVTOOLS_reindex_large.json) with `?scroll=10m&wait_for_completion=false`; poll `GET _tasks/<task_id>` for progress.

4. **Multi-index**  
   Generate reindex requests, e.g.  
   `python multi_index_reindex.py --indices "index1,index2,index3" --source-host "https://..." --username user --password pass [--output reindex_requests.txt]`  
   or `--indices-file indices.txt` (one index per line). Add `--large` for async reindex bodies (`?scroll=10m&wait_for_completion=false`). Paste into Dev Tools and run each block. After each async reindex, note the `task` id and run `python poll_reindex_task.py --task-id "<id>"` (set `DEST_ELASTIC_*` env vars). For Logstash, use `--format list` instead of `devtools`.

5. **Validate**  
   Single index:
   ```bash
   python validate_migration.py --source-index SOURCE_INDEX --dest-index DEST_INDEX \
     --source-host "https://search-....es.amazonaws.com" \
     --dest-host "https://....found.io" --dest-api-key "KEY"
   ```
   Batch (optional `--dest-prefix` / `--dest-suffix`; `--check-existence`; `--sample-size N` for ID checks via `_mget`):
   ```bash
   python validate_migration.py --indices "idx1,idx2" --dest-prefix "migrated-" \
     --check-existence --sample-size 20
   # or: --indices-file indices.txt
   ```

## Option B: Logstash

1. **Prepare environment**  
   Copy [.env.example](.env.example) to `.env` at the repo root and set `SOURCE_OPENSEARCH_*`, `LOGSTASH_SOURCE_INDEX`, `LOGSTASH_DEST_INDEX`, and either `ELASTIC_CLOUD_ID` + `ELASTIC_CLOUD_AUTH` or (for the `apikey` compose profile) `DEST_ELASTIC_HOST` + `DEST_ELASTIC_API_KEY`. See [Logstash_input/README.md](Logstash_input/README.md).

2. **Run Logstash**  
   From [Logstash_input](Logstash_input): `docker compose up --build` (or `--profile apikey` for API-key output). For custom configs only, you can still use [sample_logstash.conf](Logstash_input/sample_logstash.conf) / [sample_Dockerfile](Logstash_input/sample_Dockerfile). For multiple indices, run one pipeline per index or use [multi_index_reindex.py](multi_index_reindex.py) to drive sequential runs.

3. **Validate**  
   Same as Option A, step 5.

## Mappings and conflicts (remote reindex)

- Prefer **pre-creating** the destination index on Elastic with explicit mappings if you need strict types; otherwise reindex can create the index from the remote mapping.
- Re-run conflicts: use `POST _reindex?conflicts=proceed` (optionally with `scroll` / `wait_for_completion=false`) so version conflicts do not abort the whole job. See [Remote_Reindex/README.md](Remote_Reindex/README.md).

## Serverless

- **Elastic Cloud Serverless** does not support remote reindex to the same degree as Hosted; use **Logstash** (or similar) to push into Serverless. See [docs/SERVERLESS.md](docs/SERVERLESS.md).
- **Amazon OpenSearch Serverless** as a source often requires Logstash or a custom signing client; see [docs/SERVERLESS.md](docs/SERVERLESS.md).

## Dual-write and cutover

Use when you need **zero or minimal downtime** and can change the application.

**Dual-write (transition period)**

1. Application writes new data to **both** Amazon OpenSearch and Elastic (same logical document IDs when possible).
2. Backfill history with **remote reindex** or **Logstash** until Elastic has the same corpus (or acceptable lag).
3. Run **validate_migration.py** (counts and optional `--sample-size`) on critical indices.
4. If using time-based routing, align clocks and use **idempotent** writes or deterministic IDs so duplicate detection is predictable.

**Cutover checklist**

1. **Freeze or drain** writes to OpenSearch (maintenance window or feature flag).
2. Run a **final incremental** sync (reindex with time filter, or last Logstash pass).
3. **Validate** again on Elastic.
4. **Switch reads** to Elastic (connection strings, search clients).
5. **Monitor** latency, errors, and result quality.
6. **Decommission** OpenSearch after a safe observation period; revoke migration credentials.

Rollback: point reads (and writes if still dual-writing) back to OpenSearch until Elastic is fixed.

## Rollback

- **Reindex:** Delete the destination index on Elastic (e.g. `DELETE /destinationindexname`) and re-run the reindex.
- **Logstash:** Delete the destination index and re-run the pipeline (optionally with a bookmark if you added one).
