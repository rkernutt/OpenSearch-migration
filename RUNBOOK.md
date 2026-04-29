# Runbook: Amazon OpenSearch Service → Elastic Cloud migration

Use this runbook for a single migration or a multi-index migration.

**Environment:** Steps assume connectivity and credentials work in **your** context. If something fails, check network routes (including proxy/allowlist), IAM/fine-grained roles, cluster load, and mapping compatibility—issues are often environmental. Pilot on a non-production index and re-run [validate_migration.py](../validate_migration.py) after changes.

**First time using this repo?** See [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) and [examples/env/](examples/env/) for `.env` templates.

Before production cutover, run a **smoke test** and optional **`pytest`** checks—see [docs/TESTING.md](docs/TESTING.md).

For a **copy-paste org template** (RACI, links, checklists), see [docs/RUNBOOK_TEMPLATE.md](docs/RUNBOOK_TEMPLATE.md).

**CLI / CI / orchestration:** [Makefile](Makefile), [preflight.py](preflight.py), [docs/AUTOMATION.md](docs/AUTOMATION.md) (exit codes); [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md) (Tines, AWS Step Functions, Jenkins).

## Prerequisites

- **Elastic Cloud Hosted** deployment if using **remote reindex** (remote reindex is not supported on Elastic **Serverless**—use Logstash, Kafka-buffered ETL, or custom bulk; see [docs/SERVERLESS.md](docs/SERVERLESS.md)).
- OpenSearch domain reachable from Elastic Cloud (public endpoint or proxy). If the domain is VPC-only, use a public endpoint or the [Proxy](Proxy/README.md) in AWS that signs requests with SigV4.
- Credentials:
  - **Remote reindex:** OpenSearch username/password (fine-grained access), or ensure the domain accepts requests from Elastic’s IPs with appropriate auth.
  - **Logstash:** OpenSearch user/password, or IAM + proxy (see [Logstash_input/README.md](Logstash_input/README.md)).
  - **Kafka path (optional):** see [docs/KAFKA_MIGRATION.md](docs/KAFKA_MIGRATION.md).
  - **Elastic:** API key or username/password for the Elastic deployment.

## Version and compatibility (before you migrate)

Three different “version” problems are often confused:

1. **On-disk / Lucene segment format**  
   Errors about index creation version or “future” Lucene majors usually apply to **snapshot restore** or **binary** index copies. **Remote reindex** and **document replay** (scroll/PIT/bulk/Logstash) **write new segments** on Elastic and typically **avoid** this class of problem.

2. **Elasticsearch document `_version` / external versioning**  
   If bulk writes use **external** versioning, replays can **conflict**. For migration, prefer **omitting** external version on the first load, or use `POST _reindex?conflicts=proceed` for idempotent reruns. See [Remote_Reindex/README.md](Remote_Reindex/README.md).

3. **Extra fields in `_source`** (e.g. legacy `version`, noisy metadata)  
   Strip with a reindex **`script`** ([Remote_Reindex/Elastic_DEVTOOLS_reindex_with_script.json](Remote_Reindex/Elastic_DEVTOOLS_reindex_with_script.json)) or Logstash `mutate`. Note: sample pipelines remove Logstash’s **`@version`** field only—that is **not** the cluster index-format version.

**Snapshot restore** from OpenSearch into Elasticsearch is generally **not** a supported cross-product path for arbitrary versions—prefer **reindex** or **streaming ETL**.

## Ordering guarantees (FIFO-style)

Elasticsearch applies updates **in parallel across shards**; **global strict FIFO** for an entire index is not guaranteed.

- **Same `_id` (causal updates):** use **one writer path per index** (or partition by key). For Logstash: `pipeline.workers => 1` and `pipeline.ordered => true` ([Logstash_input/README.md](Logstash_input/README.md)).
- **Kafka:** set the **record key** to `_id` (or business key) so updates for that id land in one partition; see [docs/KAFKA_MIGRATION.md](docs/KAFKA_MIGRATION.md).
- **Remote reindex:** order follows **scroll/PIT** iteration, not necessarily **business event time**. For time-ordered backfills, use a **sort** on a timestamp and/or **time-sliced** jobs.

## Failure, retries, and replay

- **Idempotent `_id`:** keep OpenSearch `_id` on the destination so retries do not duplicate documents.
- **Remote reindex:** use async tasks; on failure, investigate `GET _tasks/<id>` and rerun with `conflicts=proceed` where appropriate.
- **Logstash:** enable Elasticsearch **output retries**; configure **dead letter queue (DLQ)** for poison events; for resumable chunks, prefer **time-bounded queries** in a custom pipeline.
- **Kafka:** at-least-once consumers + **idempotent** bulk by `_id`; **dead-letter topic**; replay from retained history—[docs/KAFKA_MIGRATION.md](docs/KAFKA_MIGRATION.md).
- **Checkpointed extract:** if you must minimize re-reading OpenSearch, consider a small **PIT + `search_after`** worker—[docs/CHECKPOINT_ETL.md](docs/CHECKPOINT_ETL.md).

After any phase, run [validate_migration.py](validate_migration.py).

## Large index throughput checklist

Use this when migrations must finish in **hours**, not weeks:

| Step | Action |
|------|--------|
| Destination index | Pre-create with `refresh_interval: -1` and `number_of_replicas: 0` ([Remote_Reindex/Elastic_destination_index_settings.json](Remote_Reindex/Elastic_destination_index_settings.json)); restore production settings **after** load. |
| Remote reindex | Use `wait_for_completion=false`, tune `scroll`, **`size`**, **`socket_timeout`** ([Remote_Reindex/Elastic_DEVTOOLS_reindex_large.json](Remote_Reindex/Elastic_DEVTOOLS_reindex_large.json)); consider **`slices`** for parallel work when ordering constraints allow. |
| Concurrency | Cap **parallel indices / slices** so source and **Elastic** ingest threads are not saturated; watch OpenSearch `Threadpool` rejections and Elastic bulk latency. |
| Ordering vs speed | **Do not** massively parallelize if you require strict **per-id** ordering; trade throughput for a **single worker** or **Kafka key = `_id`**. |
| Post-migrate | `POST <index>/_settings` to set `refresh_interval` (e.g. `1s`) and `number_of_replicas` as required; optionally `_forcemerge` only after understanding merge cost. |

## Packaging (Docker vs local Logstash)

Docker Compose is the **default** path ([Logstash_input/README.md](Logstash_input/README.md)). For hosts without Docker, see [docs/PACKAGING.md](docs/PACKAGING.md).

## Semantic / vector fields (phase 2)

For `knn_vector`, OpenSearch **semantic**, and Elasticsearch **`semantic_text`** / inference, see [docs/SEMANTIC_MIGRATION.md](docs/SEMANTIC_MIGRATION.md) and [examples/semantic_text/](examples/semantic_text/).

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
   Copy [.env.example](.env.example) to `.env` at the repo root and set `SOURCE_OPENSEARCH_*`, `LOGSTASH_SOURCE_INDEX`, `LOGSTASH_DEST_INDEX`, and either `ELASTIC_CLOUD_ID` + `ELASTIC_CLOUD_AUTH` or (for the `apikey` compose profile) `DEST_ELASTIC_HOST` + `DEST_ELASTIC_API_KEY`. See [Logstash_input/README.md](Logstash_input/README.md) and [docs/PACKAGING.md](docs/PACKAGING.md).

2. **Run Logstash**  
   From [Logstash_input](Logstash_input): `docker compose up --build` (or `--profile apikey` for API-key output). For custom configs only, you can still use [sample_logstash.conf](Logstash_input/sample_logstash.conf) / [sample_Dockerfile](Logstash_input/sample_Dockerfile). For multiple indices, run one pipeline per index or use [multi_index_reindex.py](multi_index_reindex.py) to drive sequential runs.

3. **Validate**  
   Same as Option A, step 5.

## Mappings and conflicts (remote reindex)

- Prefer **pre-creating** the destination index on Elastic with explicit mappings if you need strict types; otherwise reindex can create the index from the remote mapping.
- Re-run conflicts: use `POST _reindex?conflicts=proceed` (optionally with `scroll` / `wait_for_completion=false`) so version conflicts do not abort the whole job. See [Remote_Reindex/README.md](Remote_Reindex/README.md).

## Option C: Kafka buffer (optional)

For durable replay and scaled consumers, see [docs/KAFKA_MIGRATION.md](docs/KAFKA_MIGRATION.md). Often combined with Logstash or a custom harvester.

## Option D: S3 staging (`s3_migration` — extract → S3 → load)

Decoupled extract and load via gzipped NDJSON parts in S3. Works to **Elastic Serverless** as well as Hosted, and is friendly to VPC-only sources (run from a host inside the VPC, or point at the in-repo SigV4 reverse proxy).

1. **Extract** to S3:
   ```bash
   python -m s3_migration.s3_extract \
     --source-host "$SOURCE_OPENSEARCH_HOST" \
     --source-user "$SOURCE_OPENSEARCH_USER" \
     --source-password "$SOURCE_OPENSEARCH_PASSWORD" \
     --indices "logs-2024,metrics-2024" \
     --s3-uri "s3://my-bucket/migration/2026-04-29/" \
     --slices 4 --strict-exit-codes --log-format json \
     --checkpoint-file ./.extract.ckpt
   ```
   SigV4 mode is the default when basic credentials aren't supplied. Add `--via-proxy` when routing through the in-repo proxy so the manifest tags `auth: proxy`.

2. **Load** the same prefix into Elastic:
   ```bash
   python -m s3_migration.s3_bulk_load \
     --s3-uri "s3://my-bucket/migration/2026-04-29/" \
     --dest-host "$DEST_ELASTIC_HOST" --dest-api-key "$DEST_ELASTIC_API_KEY" \
     --strict-exit-codes --log-format json \
     --checkpoint-file ./.bulk-load.ckpt
   ```
   Per-document failures land under `s3://.../dlq/` by default; rerun the loader against the DLQ prefix to retry.

3. **Validate** (same script as Option A/B):
   ```bash
   python validate_migration.py \
     --indices "logs-2024,metrics-2024" \
     --check-existence --sample-size 50 \
     --strict-exit-codes --output-format json
   ```

For full architecture, format spec, tuning checklist, and troubleshooting, see [docs/S3_MIGRATION.md](docs/S3_MIGRATION.md). Logstash variant for source-only NDJSON: [Logstash_input/pipeline/logstash_s3.conf](Logstash_input/pipeline/logstash_s3.conf) (`docker compose --profile s3 up`).

## Option E: Reindex-from-Snapshot (wrapped upstream RFS)

When you already snapshot OpenSearch / Elasticsearch to S3, the upstream OpenSearch Migrations RFS tool reads those snapshots directly (Lucene-aware) and bulk-indexes to Elastic — including **Elastic Serverless** via `--target-type ELASTICSEARCH_SERVERLESS`. This repo's [`s3_migration.rfs_runner`](s3_migration/rfs_runner.py) is a thin Python wrapper around the upstream container; it streams logs and auto-runs `validate_migration.py` afterwards.

```bash
export RFS_UPSTREAM_IMAGE="ghcr.io/your-org/opensearch-migrations@sha256:<digest>"

python -m s3_migration.rfs_runner \
  --upstream-image "$RFS_UPSTREAM_IMAGE" \
  --snapshot-name snap-2026-04-29 \
  --s3-repo-uri s3://my-os-snapshots/production/repo \
  --s3-region us-east-1 \
  --target-host "$DEST_ELASTIC_HOST" \
  --target-api-key "$DEST_ELASTIC_API_KEY" \
  --target-type ELASTICSEARCH_SERVERLESS \
  --source-version OpenSearch_2_13 \
  --indices-validate "logs-2024,metrics-2024" --validate-sample-size 50 \
  --strict-exit-codes
```

For AWS, [`iac/terraform/rfs-fargate/`](iac/terraform/rfs-fargate/) provisions the same image as a Fargate task (S3 read-only on the snapshot bucket, API key from Secrets Manager). For multi-worker fan-out, [`iac/terraform/rfs-orchestration/`](iac/terraform/rfs-orchestration/) wraps it in a Step Functions Map state. Full guide and troubleshooting: [docs/RFS.md](docs/RFS.md).

## Option F: Capture & replay (cutover validation)

Sampled real-traffic validation. Enable capture on the existing SigV4 proxy and replay later against the destination. Lightweight Python equivalent of upstream's Java capture/replay pipeline; suitable for cutover gates, **not** for petabyte-scale traffic mirroring.

```bash
# 1) Enable capture on the proxy (local file mode shown; s3:// also supported)
export OPENSEARCH_ENDPOINT="https://vpc-xxx.us-east-1.es.amazonaws.com"
export PROXY_CAPTURE_MODE=local
export PROXY_CAPTURE_DIR=/var/log/proxy-capture
export PROXY_CAPTURE_PATH_INCLUDE='/_search,/_msearch'
export PROXY_CAPTURE_METHODS='GET,POST'
python -m Proxy.app

# 2) Run real / synthetic traffic against the proxy as you normally would.

# 3) Replay a sampled subset against the destination
python -m replay.replayer \
  --captures /var/log/proxy-capture/ \
  --dest-host "$DEST_ELASTIC_HOST" \
  --dest-api-key "$DEST_ELASTIC_API_KEY" \
  --method GET,POST \
  --path-include '/_search$' \
  --max-requests 5000 --rate-limit 50 \
  --size-tolerance 0.10 \
  --report ./replay-report.json \
  --strict-exit-codes --log-format json
```

Pair with `shadow_diff` (curated query parity) for a complete cutover gate — both must exit 0 before flipping traffic. Full guide: [docs/CAPTURE_REPLAY.md](docs/CAPTURE_REPLAY.md), [docs/SHADOW_DIFF.md](docs/SHADOW_DIFF.md).

## Serverless

- **Elastic Cloud Serverless** does not support remote reindex to the same degree as Hosted; use **Option D** ([S3 staging](docs/S3_MIGRATION.md)), **Option E** ([wrapped RFS](docs/RFS.md)), **Option F** ([capture & replay](docs/CAPTURE_REPLAY.md)) for cutover validation, **Logstash**, **Kafka + consumer**, or **custom bulk** to push into Serverless. See [docs/SERVERLESS.md](docs/SERVERLESS.md).
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

## Running Logstash and validate_migration.py in parallel

You can validate progress while Logstash is still running, but be aware of the following:

**Elasticsearch `refresh_interval`**: By default, newly indexed documents are only visible to search after a refresh (default `1s`; set to `-1` during bulk migration). This means `validate_migration.py` may see a lower document count on the destination than actually exists on disk. This lag is typically under 1–5 seconds with default settings, but with `refresh_interval: -1` it will persist until you manually refresh.

**Workflow:**

```bash
# Terminal 1: run Logstash (from Logstash_input/)
docker compose up --build

# Terminal 2: periodically check progress (run as many times as you like)
python validate_migration.py \
  --source-index logs-2024 \
  --dest-index logs-2024 \
  --source-host "$SOURCE_OPENSEARCH_HOST" \
  --dest-host "$DEST_ELASTIC_HOST" \
  --dest-api-key "$DEST_ELASTIC_API_KEY"
```

**Expected output during active migration:** count mismatches are normal until Logstash completes. Do not treat a `FAIL` during active Logstash runs as a problem — only the final validation after Logstash stops matters.

**Forcing a refresh before final validation:**

```bash
# On the Elasticsearch destination, force all pending writes to be visible:
curl -X POST "$DEST_ELASTIC_HOST/logs-2024/_refresh" \
  -H "Authorization: ApiKey $DEST_ELASTIC_API_KEY"
```

Then re-run `validate_migration.py` for the final count check.

**Using `--check-existence` during active migration:** safe to use at any point; it will confirm the index exists without blocking on counts.

## Rollback

- **Reindex:** Delete the destination index on Elastic (e.g. `DELETE /destinationindexname`) and re-run the reindex.
- **Logstash:** Delete the destination index and re-run the pipeline (optionally with a bookmark if you added one).
