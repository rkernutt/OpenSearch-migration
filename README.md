# OpenSearch migration to Elastic

This project helps you read indexes from an OpenSearch cluster and migrate or reindex them to Elastic. Use it to periodically ingest data into Elastic, or to migrate once and retire OpenSearch.

**Security:** Do not commit secrets. Use [.env.example](.env.example) and [.gitignore](.gitignore); see [SECURITY.md](SECURITY.md) for least-privilege IAM, credential hygiene, and CI scanning tips.

**Testing:** See [docs/TESTING.md](docs/TESTING.md) for smoke tests and `pytest` (offline CLI checks).

## Migration paths into Elastic Cloud

Two methods move data **from Amazon OpenSearch Service (or any OpenSearch cluster) into Elastic Cloud**:

- **Remote reindex** – Run `POST _reindex` from Kibana/Dev Tools on your **Elastic** deployment, with `source.remote` pointing at the OpenSearch domain. See [Remote_Reindex](Remote_Reindex/).
- **Logstash** – Run a Logstash pipeline with the OpenSearch input plugin reading from OpenSearch and the Elasticsearch output writing to Elastic Cloud. See [Logstash_input](Logstash_input/).

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

- **Remote reindex:** Configure the allowlist on Elastic Cloud, then run the reindex request from Dev Tools against your Elastic deployment.
- **Logstash:** Build and run the Docker image (or run Logstash with the sample config), and set the OpenSearch URL, Elastic Cloud `cloud_id`, and credentials via env or config.
- **VPC access:** Deploy the [Proxy](Proxy/README.md) if the OpenSearch domain is not reachable from Elastic Cloud or from your Logstash host. A starter **ALB + security group** sketch is in [iac/terraform/proxy-alb](iac/terraform/proxy-alb).
- **Multiple indices:** Use `multi_index_reindex.py` (`--indices` / `--indices-file`, optional `--large`) and [RUNBOOK.md](RUNBOOK.md). Poll async tasks with `poll_reindex_task.py`.

## Further improvements

See [RECOMMENDATIONS.md](RECOMMENDATIONS.md) for remaining optional follow-up ideas.
