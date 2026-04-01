# Recommendations and optional backlog

The **OpenSearch → Elastic Cloud** migration toolkit in this repository is **feature-complete** for the planned scope, including **org-level** guidance and building blocks:

- [docs/ORG_PRODUCTION_IAC.md](docs/ORG_PRODUCTION_IAC.md) — production Terraform (IAM scope, secrets, VPC endpoints, WAF, autoscaling).
- [docs/TLS_AND_CREDENTIAL_LIFECYCLE.md](docs/TLS_AND_CREDENTIAL_LIFECYCLE.md) — TLS, cert, key, and allowlist lifecycle.
- [validate_migration.py](validate_migration.py) — sampling modes: `head`, `random`, `stratified` (slice), **`time_stratified`** (stats buckets on `--time-field`).

This page records **where each area was implemented**. Policy choices (approval tiers, exact rotation dates) remain **your org’s** to finalize in internal wiki/tickets.

---

## Implementation map (done)

| Theme | Where it lives |
|--------|----------------|
| Security & secrets | [SECURITY.md](SECURITY.md), [.env.example](.env.example), [examples/env/](examples/env/), [.gitignore](.gitignore), [bootstrap_env.py](bootstrap_env.py) |
| First-time users | [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) |
| Org production IaC checklist | [docs/ORG_PRODUCTION_IAC.md](docs/ORG_PRODUCTION_IAC.md), [iac/](iac/) (WAF, scoped IAM, Secrets Manager inject, ECS autoscaling) |
| TLS & credential lifecycle | [docs/TLS_AND_CREDENTIAL_LIFECYCLE.md](docs/TLS_AND_CREDENTIAL_LIFECYCLE.md) |
| Validation (batch, sampling, existence) | [validate_migration.py](validate_migration.py), [README.md](README.md), [RUNBOOK.md](RUNBOOK.md) |
| Multi-index + large async reindex | [multi_index_reindex.py](multi_index_reindex.py), [Remote_Reindex/](Remote_Reindex/) |
| Task polling | [poll_reindex_task.py](poll_reindex_task.py), [Remote_Reindex/README.md](Remote_Reindex/README.md) |
| Mappings & conflicts | [Remote_Reindex/README.md](Remote_Reindex/README.md), [RUNBOOK.md](RUNBOOK.md) |
| Serverless (Elastic + OpenSearch) | [docs/SERVERLESS.md](docs/SERVERLESS.md) |
| Dual-write & cutover | [RUNBOOK.md](RUNBOOK.md) |
| Logstash Docker + `.env` | [Logstash_input/Dockerfile](Logstash_input/Dockerfile), [Logstash_input/docker-compose.yml](Logstash_input/docker-compose.yml), [Logstash_input/pipeline/](Logstash_input/pipeline/), [Logstash_input/README.md](Logstash_input/README.md) |
| Testing & CI | [docs/TESTING.md](docs/TESTING.md), [pytest.ini](pytest.ini), [.github/workflows/ci.yml](.github/workflows/ci.yml), [.github/workflows/ci-security-strict.yml](.github/workflows/ci-security-strict.yml), [tests/](tests/) |
| Reindex with `script` example | [Remote_Reindex/Elastic_DEVTOOLS_reindex_with_script.json](Remote_Reindex/Elastic_DEVTOOLS_reindex_with_script.json) |
| Org runbook template | [docs/RUNBOOK_TEMPLATE.md](docs/RUNBOOK_TEMPLATE.md) |
| Runbook: version, FIFO, retries, throughput | [RUNBOOK.md](RUNBOOK.md) |
| Kafka buffer architecture | [docs/KAFKA_MIGRATION.md](docs/KAFKA_MIGRATION.md) |
| Docker vs local Logstash | [docs/PACKAGING.md](docs/PACKAGING.md) |
| Checkpointed ETL evaluation | [docs/CHECKPOINT_ETL.md](docs/CHECKPOINT_ETL.md) |
| Semantic / vector migration | [docs/SEMANTIC_MIGRATION.md](docs/SEMANTIC_MIGRATION.md), [examples/semantic_text/](examples/semantic_text/) |

---

## How to run automated checks

```bash
pip install -r requirements-dev.txt   # or pip3
python3 -m pytest -q
```

Smoke and manual procedures: [docs/TESTING.md](docs/TESTING.md).

---

## Historical note

Earlier versions of this file listed numbered recommendation sections (security, validation, multi-index, mappings, serverless, cutover, ops, testing). Those items are **implemented** as shown in the table above; the numbered list was replaced by this map to avoid duplication with the rest of the documentation.
