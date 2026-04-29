# OpenSearch migration to Elastic

This project helps you read indexes from an OpenSearch cluster and migrate or reindex them to Elastic. Use it to periodically ingest data into Elastic, or to migrate once and retire OpenSearch.

**Real-world caveat:** behavior and performance depend on **your environment**—cluster versions, networking (VPC, TLS, proxies, firewalls), IAM and throttling limits, index size and mapping quirks, and custom OpenSearch plugins. Expect to **test in non-production**, use [preflight.py](preflight.py) / [validate_migration.py](validate_migration.py), and adjust for your setup. See **Caveats and scope** below.

**New here?** Start with **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)** (clone → `.env` → migrate → validate). Example env templates: **[examples/env/](examples/env/)** (validation-only, Logstash with Cloud ID, Logstash with API key). Full variable reference: **[.env.example](.env.example)**.

**Security:** Do not commit secrets. Use [.env.example](.env.example) and [.gitignore](.gitignore); see [SECURITY.md](SECURITY.md) for least-privilege IAM, credential hygiene, and CI scanning tips.

**Testing:** See [docs/TESTING.md](docs/TESTING.md) for smoke tests and `pytest` (offline CLI checks).

**CLI / CI / orchestration:** [Makefile](Makefile) (`make test`, `make lint`, `make preflight`, `make validate`), exit codes in [docs/AUTOMATION.md](docs/AUTOMATION.md), and platform notes (Tines, Step Functions, Jenkins) in [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md). **Tines story blueprint:** [docs/TINES_STORY_TEMPLATE.md](docs/TINES_STORY_TEMPLATE.md). Version expectations: [docs/VERSION_MATRIX.md](docs/VERSION_MATRIX.md).

**One CLI to rule them all:** [`migrate.py`](migrate.py) is an umbrella entry point that dispatches to every script in this repo (`migrate preflight`, `migrate s3-load`, `migrate metadata`, `migrate shadow-diff`, `migrate replay`, …). `pip install -e .` exposes it as the `migrate` console script. See [docs/TOOLS.md](docs/TOOLS.md) for the full CLI index.

**License:** [LICENSE](LICENSE) (Apache-2.0); attributions: [NOTICE](NOTICE). **Changelog:** [CHANGELOG.md](CHANGELOG.md).

### Documentation map

| Doc | Purpose |
|-----|---------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture and process flow diagrams (Mermaid) |
| [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) | First-time setup, `.env`, validate |
| [RUNBOOK.md](RUNBOOK.md) | Migration procedures, versioning, throughput |
| [docs/AUTOMATION.md](docs/AUTOMATION.md) | Exit codes, `make`, `multi_index` env |
| [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md) | Tines, Step Functions, Jenkins |
| [docs/TINES_STORY_TEMPLATE.md](docs/TINES_STORY_TEMPLATE.md) | Tines story blueprint |
| [docs/SERVERLESS.md](docs/SERVERLESS.md) | Elastic / OpenSearch Serverless |
| [docs/S3_MIGRATION.md](docs/S3_MIGRATION.md) | S3 staging path (extract → S3 → load) |
| [docs/RFS.md](docs/RFS.md) | Snapshot path via wrapped upstream RFS |
| [docs/METADATA_MIGRATION.md](docs/METADATA_MIGRATION.md) | Templates / pipelines / sanitizers (Serverless + multi-version) |
| [docs/SHADOW_DIFF.md](docs/SHADOW_DIFF.md) | Query-parity cutover gate (`shadow_diff.py`) |
| [docs/CAPTURE_REPLAY.md](docs/CAPTURE_REPLAY.md) | Sampled traffic capture & replay (Path F) |
| [docs/TOOLS.md](docs/TOOLS.md) | Single index of every CLI script with one-line description and links |
| [`migrate.py`](migrate.py) | Umbrella `migrate <subcommand>` CLI (one entry point for every script in this repo) |
| [docs/KAFKA_MIGRATION.md](docs/KAFKA_MIGRATION.md) | Kafka buffer pattern |
| [docs/SEMANTIC_MIGRATION.md](docs/SEMANTIC_MIGRATION.md) | Vectors / `semantic_text` |
| [docs/TESTING.md](docs/TESTING.md) | pytest, sampling, integration |
| [docs/VERSION_MATRIX.md](docs/VERSION_MATRIX.md) | Version expectations |
| [SECURITY.md](SECURITY.md) | Secrets, IAM, CI scanning |
| [RECOMMENDATIONS.md](RECOMMENDATIONS.md) | What lives where in the repo |

### Caveats and scope

- **Six data paths, not interchangeable:** **Remote reindex** needs **Elastic Cloud Hosted** and network access from Elastic to OpenSearch; it does **not** apply to **Elastic Serverless** as a destination ([docs/SERVERLESS.md](docs/SERVERLESS.md)). **Logstash** (or similar) is the usual answer for streaming and Serverless. **S3 staging** ([docs/S3_MIGRATION.md](docs/S3_MIGRATION.md)) decouples extract and load via gzipped NDJSON in S3 — ideal for VPC-only sources and Serverless destinations. **RFS** ([docs/RFS.md](docs/RFS.md)) wraps the upstream OpenSearch Migrations container to read S3 snapshots Lucene-natively. **Capture & replay** ([docs/CAPTURE_REPLAY.md](docs/CAPTURE_REPLAY.md)) is a *cutover-validation* path, not a primary load path: the SigV4 proxy tees traffic to NDJSON and a replayer reissues a sampled subset against the destination. **Kafka** is documented as an **optional architecture** (buffer/replay)—this repo does **not** ship a full Kafka/Connect stack; you operate brokers and consumers in your environment ([docs/KAFKA_MIGRATION.md](docs/KAFKA_MIGRATION.md)).
- **Cutover gates, not just data movement:** [`shadow_diff.py`](shadow_diff.py) replays curated queries against source + dest and exits non-zero on drift; the capture/replay path validates sampled real traffic. Both are **gates**, not loaders — pair them with a primary path before flipping production traffic.
- **Metadata is migrated separately:** Index templates, component templates, and ingest pipelines are migrated by [`metadata_migration`](metadata_migration/) with optional Serverless settings sanitization and ES 5/6 multi-type mapping flatten. Run **before** the data path so destination indices are created with the right settings/mappings.
- **Orchestration examples, not exclusives:** [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md) discusses **Tines**, **AWS Step Functions**, and **Jenkins** as common ways to wrap the same CLIs and APIs. Other schedulers, runbooks, or no orchestrator at all are fine. **GitHub Actions** in this repository is for **CI on the toolkit** (tests, lint, Terraform validate)—not a required way to run production migrations.
- **Environmental factors (issues are normal until validated):** Problems often come from outside this repository: **network path** (timeouts if Elastic cannot reach OpenSearch or you rely on a proxy/ALB), **auth** (expired keys, wrong SigV4 region, FGAC too tight), **cluster limits** (threadpool rejections, max scroll/context, ingest pressure), **mapping and runtime field differences**, and **data shape** (oversized documents, nested limits). **Semantic / vector** and neural features add more surface area ([docs/SEMANTIC_MIGRATION.md](docs/SEMANTIC_MIGRATION.md)). Always run **preflight**, a **pilot index**, and **validation** in an environment that matches production; nothing here guarantees a zero-touch run for every customer topology.

## Migration paths into Elastic Cloud

Primary ways to move data **from Amazon OpenSearch Service (or any OpenSearch cluster) into Elastic**:

- **A. Remote reindex** — Run `POST _reindex` from Kibana/Dev Tools on your **Elastic** deployment, with `source.remote` pointing at the OpenSearch domain. See [Remote_Reindex](Remote_Reindex/). (**Elastic Cloud Hosted**; not Serverless—see [docs/SERVERLESS.md](docs/SERVERLESS.md).)
- **B. Logstash** — OpenSearch input → Elasticsearch output. See [Logstash_input](Logstash_input/).
- **C. Kafka (optional)** — Buffer and replay between extract and load; see [docs/KAFKA_MIGRATION.md](docs/KAFKA_MIGRATION.md). Architecture-only; you operate the brokers and consumers.
- **D. S3 staging** — Extract OpenSearch to gzipped NDJSON in S3, then bulk-load into Elastic (Hosted **or** Serverless). VPC- and air-gap-friendly. See [docs/S3_MIGRATION.md](docs/S3_MIGRATION.md) and [`s3_migration/`](s3_migration/).
- **E. Reindex-from-Snapshot (wrapped)** — Read existing S3 snapshots via the upstream OpenSearch Migrations RFS image. Best for very large snapshots and Serverless destinations. See [docs/RFS.md](docs/RFS.md), [`iac/terraform/rfs-fargate/`](iac/terraform/rfs-fargate/), and [`iac/terraform/rfs-orchestration/`](iac/terraform/rfs-orchestration/) (parallel fan-out via Step Functions).
- **F. Capture & replay (cutover validation)** — Tee proxied traffic to NDJSON via [`Proxy/capture.py`](Proxy/capture.py), then sample-replay it against the destination with [`replay/replayer.py`](replay/replayer.py). See [docs/CAPTURE_REPLAY.md](docs/CAPTURE_REPLAY.md). **Not** a primary load path; pair with one of A–E and use the replay output as a cutover gate.

Two extra kinds of work that pair with any data path:

- **Metadata migration** — [`metadata_migration/`](metadata_migration/) copies index templates, component templates, and ingest pipelines from source to dest with optional Serverless settings sanitization and ES 5/6 multi-type mapping flatten. Run **before** the data path. See [docs/METADATA_MIGRATION.md](docs/METADATA_MIGRATION.md).
- **Cutover gates** — [`shadow_diff.py`](shadow_diff.py) (curated query parity) and the replay path (sampled real-traffic parity) both exit non-zero on drift. Run **after** the data path and **before** flipping production traffic. See [docs/SHADOW_DIFF.md](docs/SHADOW_DIFF.md) and [docs/CAPTURE_REPLAY.md](docs/CAPTURE_REPLAY.md).

### Choosing a path

| Best when… | Pick | Notes |
|------------|------|-------|
| Elastic Hosted can reach OpenSearch directly; one-off batch | **A. Remote reindex** | Lowest ops footprint; not Serverless. |
| Streaming or Serverless destination; you want a pipeline you can filter | **B. Logstash** | Works to Hosted and Serverless. |
| You already have S3 snapshots; multi-TB; Lucene-aware | **E. RFS (wrapped)** | Hosted **or** Serverless via `--target-type ELASTICSEARCH_SERVERLESS`. |
| Multi-hundred-GB to multi-TB *without* a snapshot, Serverless dest, or VPC-only source | **D. S3 staging** | Decouples extract and load; resumable; works through the SigV4 proxy. |
| You already operate Kafka and want durable replay / multi-consumer | **C. Kafka** | Architecture-only; you write the harvester / consumer. |
| Validating destination parity before the cutover | **F. Capture & replay** + **shadow_diff** | These are *gates*, not loaders. |

**Operations guide:** [RUNBOOK.md](RUNBOOK.md) (versioning, ordering, retries, throughput checklist, all six options). **Packaging:** [docs/PACKAGING.md](docs/PACKAGING.md). **Semantic / vectors:** [docs/SEMANTIC_MIGRATION.md](docs/SEMANTIC_MIGRATION.md), [examples/semantic_text/](examples/semantic_text/).

## Remote reindex (to Elastic Cloud)

1. **Allowlist on Elastic Cloud Hosted**  
   In your Elastic Cloud deployment, open **Edit deployment** → **User settings** (e.g. `elasticsearch.yml`) and add:
   ```yaml
   reindex.remote.whitelist: ["your-opensearch-domain-endpoint:443"]
   ```
   Use the exact host and port (e.g. `search-your-domain-xxx.region.es.amazonaws.com:443`). For Amazon OpenSearch Service (public CA), no SSL certificate settings are needed.

2. **Run reindex**  
   In Kibana Dev Tools on the **Elastic** deployment, run the request from `Remote_Reindex/Elastic_DEVTOOLS_reindex.json` (or the large-dataset variant). The reindex runs on Elastic and pulls from the remote OpenSearch domain.

3. **VPC domains**  
   If the OpenSearch domain has no public endpoint, Elastic Cloud cannot reach it. Use a public endpoint for the domain (if allowed) or run the [Proxy](Proxy/README.md) in AWS (optionally behind an ALB with TLS) so Elastic Cloud can call it and the proxy forwards to the domain with SigV4.

**Note:** Remote reindex is supported on **Elastic Cloud Hosted** only, not on **Elastic Cloud Serverless**. For Serverless destinations—or OpenSearch Serverless as a source—see [docs/SERVERLESS.md](docs/SERVERLESS.md) and the Logstash path.

## Logstash

The Logstash pipeline uses the `logstash-input-opensearch` plugin to read from OpenSearch and the built-in Elasticsearch output to write to Elastic Cloud. See [Logstash_input](Logstash_input/) for **Docker Compose + `.env`** (recommended), sample configs, and Dockerfile. For domains that use **IAM (SigV4)** only, use the [Proxy](Proxy/README.md) or fine-grained access—[Logstash_input/README.md](Logstash_input/README.md).

## Validation after migration

After each reindex or Logstash run, compare document counts between source and destination:

```bash
pip install -r requirements.txt
export SOURCE_OPENSEARCH_HOST="https://search-your-domain.region.es.amazonaws.com"
export DEST_ELASTIC_HOST="https://your-deployment.es.us-east-1.aws.found.io"
export DEST_ELASTIC_API_KEY="your-api-key"   # or DEST_ELASTIC_USER + DEST_ELASTIC_PASSWORD
python validate_migration.py --source-index myindex --dest-index myindex
```

If the source uses basic auth instead of SigV4, set `SOURCE_OPENSEARCH_USER` and `SOURCE_OPENSEARCH_PASSWORD`. See [RUNBOOK.md](RUNBOOK.md) for full steps.

**Batch validation** (several source indices, same rules for `dest` naming as `multi_index_reindex.py`):

```bash
python validate_migration.py \
  --indices "logs-2024,metrics-2024" \
  --dest-prefix "migrated-" \
  --check-existence --sample-size 25
```

**Index list file** (one name per line, `#` starts a comment):

```bash
python validate_migration.py --indices-file my_indices.txt --sample-size 50
```

`--sample-size N` loads `_search` on the source and checks those IDs on the destination via `_mget`. Modes: `--sample-mode head` (default, `_doc`), `random`, `stratified` (sliced `random_score`; tune with `--sample-slices`), or `time_stratified` (stats **min/max** buckets on `--time-field`, e.g. `@timestamp`). Machine-readable output: `--output-format json` or `csv` (see [docs/TESTING.md](docs/TESTING.md)).

## Quick start

- **Remote reindex (Path A):** Configure the allowlist on Elastic Cloud, then run the reindex request from Dev Tools against your Elastic deployment.
- **Logstash (Path B):** Build and run the Docker image (or run Logstash with the sample config), and set the OpenSearch URL, Elastic Cloud `cloud_id`, and credentials via env or config.
- **S3 staging (Path D):** `migrate s3-extract --indices ... --s3-uri s3://...` then `migrate s3-load --s3-uri s3://...`. See [docs/S3_MIGRATION.md](docs/S3_MIGRATION.md).
- **RFS (Path E):** `migrate rfs --upstream-image ... --snapshot-name ...`. See [docs/RFS.md](docs/RFS.md).
- **Capture & replay (Path F):** Set `PROXY_CAPTURE_MODE=local` (or `s3`) on the proxy, then `migrate replay --captures ...`. See [docs/CAPTURE_REPLAY.md](docs/CAPTURE_REPLAY.md).
- **Cutover gates:** `migrate shadow-diff --queries-file ./cutover-queries.json` for curated query parity. See [docs/SHADOW_DIFF.md](docs/SHADOW_DIFF.md).
- **Metadata first:** `migrate metadata --include templates,index_templates,component_templates,ingest_pipelines` before any data path. See [docs/METADATA_MIGRATION.md](docs/METADATA_MIGRATION.md).
- **VPC access:** Deploy the [Proxy](Proxy/README.md) if the OpenSearch domain is not reachable from Elastic Cloud or from your Logstash host. A starter **ALB + security group** sketch is in [iac/terraform/proxy-alb](iac/terraform/proxy-alb).
- **Multiple indices:** Use `multi_index_reindex.py` (`--indices` / `--indices-file`, optional `--large`) and [RUNBOOK.md](RUNBOOK.md). Poll async tasks with `poll_reindex_task.py`.

For a single-page index of every CLI tool in the repo, see [docs/TOOLS.md](docs/TOOLS.md).

## Further improvements

See [RECOMMENDATIONS.md](RECOMMENDATIONS.md) for the implementation map and the (small) remaining backlog.
