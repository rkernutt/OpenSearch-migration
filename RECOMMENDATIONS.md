# Recommendations and optional backlog

The **OpenSearch → Elastic Cloud** migration toolkit in this repository is **feature-complete** for the planned scope, including **org-level** guidance and building blocks:

- [docs/ORG_PRODUCTION_IAC.md](docs/ORG_PRODUCTION_IAC.md) — production Terraform (IAM scope, secrets, VPC endpoints, WAF, autoscaling).
- [docs/TLS_AND_CREDENTIAL_LIFECYCLE.md](docs/TLS_AND_CREDENTIAL_LIFECYCLE.md) — TLS, cert, key, and allowlist lifecycle.
- [validate_migration.py](validate_migration.py) — sampling modes: `head`, `random`, `stratified` (slice), **`time_stratified`** (stats buckets on `--time-field`).

This page records **where each area was implemented** and tracks open items. Policy choices (approval tiers, exact rotation dates) remain **your org’s** to finalize in internal wiki/tickets.

---

## Implementation map (done)

| Theme | Where it lives |
|--------|----------------|
| Security & secrets | [SECURITY.md](SECURITY.md), [.env.example](.env.example), [examples/env/](examples/env/), [.gitignore](.gitignore), [bootstrap_env.py](bootstrap_env.py) |
| First-time users | [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) |
| CLI / exit codes / Makefile | [docs/AUTOMATION.md](docs/AUTOMATION.md), [Makefile](Makefile) |
| Orchestration (Tines, Step Functions, Jenkins) | [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md), [docs/TINES_STORY_TEMPLATE.md](docs/TINES_STORY_TEMPLATE.md) |
| Version guidance | [docs/VERSION_MATRIX.md](docs/VERSION_MATRIX.md) |
| Preflight script | [preflight.py](preflight.py) |
| Org production IaC checklist | [docs/ORG_PRODUCTION_IAC.md](docs/ORG_PRODUCTION_IAC.md), [iac/](iac/) (WAF, scoped IAM, Secrets Manager inject, ECS autoscaling) |
| TLS & credential lifecycle | [docs/TLS_AND_CREDENTIAL_LIFECYCLE.md](docs/TLS_AND_CREDENTIAL_LIFECYCLE.md) |
| Validation (batch, sampling, existence) | [validate_migration.py](validate_migration.py), [README.md](README.md), [RUNBOOK.md](RUNBOOK.md) |
| Multi-index + large async reindex | [multi_index_reindex.py](multi_index_reindex.py), [Remote_Reindex/](Remote_Reindex/) |
| Task polling | [poll_reindex_task.py](poll_reindex_task.py), [Remote_Reindex/README.md](Remote_Reindex/README.md) |
| Mappings & conflicts | [Remote_Reindex/README.md](Remote_Reindex/README.md), [RUNBOOK.md](RUNBOOK.md) |
| Serverless (Elastic + OpenSearch) | [docs/SERVERLESS.md](docs/SERVERLESS.md) |
| Dual-write & cutover | [RUNBOOK.md](RUNBOOK.md) |
| Logstash Docker + `.env` | [Logstash_input/Dockerfile](Logstash_input/Dockerfile), [Logstash_input/docker-compose.yml](Logstash_input/docker-compose.yml), [Logstash_input/pipeline/](Logstash_input/pipeline/), [Logstash_input/README.md](Logstash_input/README.md) |
| S3 staging path (extract / load / Logstash s3 / RFS wrapper) | [s3_migration/](s3_migration/), [Logstash_input/pipeline/logstash_s3.conf](Logstash_input/pipeline/logstash_s3.conf), [iac/terraform/rfs-fargate/](iac/terraform/rfs-fargate/), [docs/S3_MIGRATION.md](docs/S3_MIGRATION.md), [docs/RFS.md](docs/RFS.md), [tests/test_s3_common.py](tests/test_s3_common.py), [tests/test_s3_bulk_load.py](tests/test_s3_bulk_load.py), [tests/test_s3_extract.py](tests/test_s3_extract.py), [tests/test_rfs_runner.py](tests/test_rfs_runner.py) |
| Parallel RFS fan-out (Step Functions) | [iac/terraform/rfs-orchestration/](iac/terraform/rfs-orchestration/), [docs/RFS.md](docs/RFS.md), `.github/workflows/ci.yml` (terraform validate step) |
| Metadata migration & sanitizers (templates / pipelines / settings / mappings) | [metadata_migration/](metadata_migration/), [docs/METADATA_MIGRATION.md](docs/METADATA_MIGRATION.md), [tests/test_metadata_sanitizer.py](tests/test_metadata_sanitizer.py), [tests/test_metadata_migrator.py](tests/test_metadata_migrator.py); covers Serverless settings stripping and ES 5/6 multi-type → typeless mapping flatten |
| Umbrella `migrate` CLI | [migrate.py](migrate.py), [docs/TOOLS.md](docs/TOOLS.md), `pyproject.toml` `[project.scripts] migrate`, [tests/test_migrate_cli.py](tests/test_migrate_cli.py) |
| Query-parity cutover gate | [shadow_diff.py](shadow_diff.py), [docs/SHADOW_DIFF.md](docs/SHADOW_DIFF.md), [tests/test_shadow_diff.py](tests/test_shadow_diff.py) |
| Capture & replay (Path F — sampled cutover validation) | [Proxy/capture.py](Proxy/capture.py), [Proxy/app.py](Proxy/app.py) (capture hook), [replay/replayer.py](replay/replayer.py), [docs/CAPTURE_REPLAY.md](docs/CAPTURE_REPLAY.md), [tests/test_proxy_capture.py](tests/test_proxy_capture.py), [tests/test_replayer.py](tests/test_replayer.py) |
| Testing & CI | [docs/TESTING.md](docs/TESTING.md), [pytest.ini](pytest.ini), [.github/workflows/ci.yml](.github/workflows/ci.yml), [.github/workflows/ci-security-strict.yml](.github/workflows/ci-security-strict.yml), [tests/](tests/) |
| Reindex with `script` example | [Remote_Reindex/Elastic_DEVTOOLS_reindex_with_script.json](Remote_Reindex/Elastic_DEVTOOLS_reindex_with_script.json) |
| Org runbook template | [docs/RUNBOOK_TEMPLATE.md](docs/RUNBOOK_TEMPLATE.md) |
| Runbook: version, FIFO, retries, throughput | [RUNBOOK.md](RUNBOOK.md) |
| Kafka buffer architecture | [docs/KAFKA_MIGRATION.md](docs/KAFKA_MIGRATION.md) |
| Docker vs local Logstash | [docs/PACKAGING.md](docs/PACKAGING.md) |
| Checkpointed ETL evaluation | [docs/CHECKPOINT_ETL.md](docs/CHECKPOINT_ETL.md) |
| Semantic / vector migration | [docs/SEMANTIC_MIGRATION.md](docs/SEMANTIC_MIGRATION.md), [examples/semantic_text/](examples/semantic_text/) |
| HTTP retry/backoff | [validate_migration.py](validate_migration.py), [preflight.py](preflight.py), [poll_reindex_task.py](poll_reindex_task.py) — `_make_session()` with `urllib3` retry on 429/500/502/503/504 |
| Configurable timeouts | [validate_migration.py](validate_migration.py) — `VALIDATION_TIMEOUT_SHORT` / `VALIDATION_TIMEOUT_SEARCH` env vars |
| Proxy hardening | [Proxy/app.py](Proxy/app.py) — `None` credential guard, `PROXY_VERIFY_TLS` / `PROXY_CA_BUNDLE`, `PROXY_DEBUG` logging, `/health` endpoint |
| Proxy API key encoding | [validate_migration.py](validate_migration.py), [poll_reindex_task.py](poll_reindex_task.py) — `--dest-api-key-encoded` flag; heuristic documented |
| Time-stratified empty buckets | [validate_migration.py](validate_migration.py) — empty bucket count included in output note |
| Preflight error detail | [preflight.py](preflight.py) — missing-index errors now include hostname |
| GUI frontend tooling | [gui/](gui/) — ESLint (`eslint.config.js`) and Prettier (`.prettierrc`) configured; `npm run lint`, `npm run format`, `npm run format:check`; [gui/README.md](gui/README.md) added |
| Structured logging | [validate_migration.py](validate_migration.py), [poll_reindex_task.py](poll_reindex_task.py) — `--log-format=json` emits one JSON object per stderr line |
| Credential masking | [multi_index_reindex.py](multi_index_reindex.py) — `--mask-credentials` replaces username/password with `***`; warning printed when credentials are plain-text in stdout |
| Index name validation | [validate_migration.py](validate_migration.py) — `validate_index_name()` rejects uppercase, invalid leading chars, and special characters before any HTTP call |
| Error message redaction | [validate_migration.py](validate_migration.py), [preflight.py](preflight.py) — `_redact_response_text()` strips `ApiKey`/`Bearer` tokens and long Base64 strings from error bodies |
| Credential rotation docs | [SECURITY.md](SECURITY.md) — "Credential rotation and long-running jobs" section: key lifetimes, mid-migration expiry recovery, post-cutover revocation |
| IAM policy for validation scripts | [SECURITY.md](SECURITY.md) — separate least-privilege read-only policy (`es:ESHttpGet/Head/Post`) for `validate_migration.py` / `preflight.py` |
| Proxy header whitelist docs | [Proxy/README.md](Proxy/README.md) — forwarded header whitelist documented with rationale; new env vars added to config table |
| Proxy dependency upper bounds | [Proxy/requirements.txt](Proxy/requirements.txt) — upper bounds added (`flask<4`, `requests<3`, `boto3<2`, `gunicorn<24`) |
| `__version__` | [version.py](version.py), [pyproject.toml](pyproject.toml) — `__version__ = "1.0.0"` in `version.py`; `[project]` table in `pyproject.toml` |
| Test coverage | [tests/test_new_utilities.py](tests/test_new_utilities.py) — 20 tests covering `validate_index_name`, `_redact_response_text`, `DestAuth` encoding, `elastic_headers_auth`, and `validate_pair` early exit |
| Production checklist | [docs/PRODUCTION_CHECKLIST.md](docs/PRODUCTION_CHECKLIST.md) — 8-section go/no-go gate: credentials, network, pilot run, destination prep, monitoring, runbook, CI, post-migration |
| Proxy Gunicorn service | [Proxy/opensearch-proxy.service](Proxy/opensearch-proxy.service) — systemd unit file; production deployment section in [Proxy/README.md](Proxy/README.md) |
| Logstash backpressure | [Logstash_input/README.md](Logstash_input/README.md) — "Handling backpressure and bulk rejections" with 6 tuning steps and DLQ guidance |
| Parallel Logstash + validation | [RUNBOOK.md](RUNBOOK.md) — new section on `refresh_interval` lag, parallel workflow, and forced refresh before final validation |

---

## How to run automated checks

```bash
pip install -r requirements-dev.txt   # or pip3
python3 -m pytest -q
```

For the GUI:

```bash
cd gui && npm install
npm run lint && npm run format:check && npm run typecheck
```

Smoke and manual procedures: [docs/TESTING.md](docs/TESTING.md).
Production go/no-go: [docs/PRODUCTION_CHECKLIST.md](docs/PRODUCTION_CHECKLIST.md).

---

## Open items / known gaps

The repository is at parity with [m-adams/opensearch-to-elasticsearch-serverless](https://github.com/m-adams/opensearch-to-elasticsearch-serverless) and broadly with the data-movement scope of upstream [opensearch-project/opensearch-migrations](https://github.com/opensearch-project/opensearch-migrations) for the OpenSearch → Elastic direction. The remaining honest gap is:

| Gap | Why it isn't shipped here | Workaround |
|-----|---------------------------|-----------|
| Kafka-backed, zero-loss, Lucene-stream traffic mirroring at petabyte scale (upstream's Java capture/replay pipeline) | Faithfully reproducing it would be 2–4 person-months and require operating Kafka. The in-repo Path F (Python proxy capture + replayer) handles **sampled cutover validation**, which is the typical use case. | Use the in-repo Path F for cutover validation; for high-fidelity production traffic mirroring at scale, run upstream's Java pipeline. |

There are no other known capability gaps relative to upstream / m-adams for the OpenSearch → Elastic direction. Bug fixes and incremental improvements track in the issue tracker.

---

## Historical note

Earlier versions of this file listed numbered recommendation sections (security, validation, multi-index, mappings, serverless, cutover, ops, testing). Those items are **implemented** as shown in the table above; the numbered list was replaced by this map to avoid duplication with the rest of the documentation.
