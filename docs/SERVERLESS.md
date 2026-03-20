# Serverless: Elastic Cloud and Amazon OpenSearch Serverless

**Last reviewed:** 2026-03-18 — verify periodically against [Elastic Cloud Serverless](https://www.elastic.co/docs/deploy-manage/deploy/cloud-on-prem/cloud) and [AWS OpenSearch Serverless](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless.html) docs (API limits and feature matrix change).

## Elastic Cloud Serverless (destination)

**Remote reindex is not supported** on Elastic Cloud Serverless deployments. The Elasticsearch `_reindex` API with `source.remote` is not available for this product model.

**Use Logstash (or another pull-based ETL)** instead:

- Run Logstash (or a custom worker) that reads from OpenSearch—using a public endpoint, fine-grained credentials, or the [Proxy](../Proxy/README.md) pattern for VPC—and writes to Serverless using the Elasticsearch output with your Serverless endpoint and API key.
- Follow [Logstash_input/README.md](../Logstash_input/README.md) and [RUNBOOK.md](../RUNBOOK.md) Option B, adjusting the output for your Serverless URL and auth.

Validate with `validate_migration.py` against the Serverless host if counts/APIs are compatible with your deployment.

---

## Amazon OpenSearch Serverless (source)

Amazon OpenSearch **Serverless** uses a different IAM and networking model than provisioned Amazon OpenSearch Service domains:

- Collections and data access policies replace the classic “domain + FGAC” model for many operations.
- Endpoints and signing can differ from provisioned `es.amazonaws.com` domains.

**Practical approach for migration to Elastic:**

1. **Prefer Logstash** (or another client that can sign requests with the correct SigV4 scope and policy for Serverless) as the replication path, similar to IAM-only provisioned domains. You may need a small adapter or proxy if your client does not support Serverless signing.
2. **Remote reindex from Elastic Hosted to a Serverless source** is uncommon and not covered in detail here; confirm network reachability and compatible authentication with AWS documentation before relying on it.
3. **Scroll / search** behavior and index listing should be tested at your expected volume; treat Serverless as a separate integration that you validate in a dev collection first.

For Elastic Cloud **Hosted**, remote reindex from a **provisioned** OpenSearch domain remains the primary API-driven path documented in [Remote_Reindex/README.md](../Remote_Reindex/README.md).
