# Production deployment checklist

Work through this list before committing to a production cutover window.
Items marked **[BLOCKING]** must be complete before migrating production
traffic; the others are strongly recommended.

Run it bottom-to-top alongside [RUNBOOK.md](../RUNBOOK.md) — the runbook
describes *how* to do each step, this checklist tracks *what* must be
true.

---

## 1. Compatibility pre-flight **[BLOCKING]**

- [ ] `migrate compat-check --strict-exit-codes --report ./compat-report.json` exits **0** for the source + destination pair
- [ ] If exit is **4**: every per-index finding is reviewed. `block-rfs` severities force Path D / B (no RFS) — recorded in the cutover plan.
- [ ] Lucene gap recorded (OS Lucene major vs ES Lucene major). For OS 3.x → ES 8.x or any other gap outside the N-1/N window, **snapshot restore / RFS is not used**.
- [ ] Custom plugins / analysers identified — if the source uses ICU, IK, synonyms, etc., the destination has the matching plugin/dictionary.

See [COMPAT_CHECK.md](COMPAT_CHECK.md) and [VERSION_MATRIX.md](VERSION_MATRIX.md).

## 2. Credentials and access **[BLOCKING]**

- [ ] Migration IAM role / user is **separate** from the production search role — minimum permissions per [SECURITY.md](../SECURITY.md)
- [ ] Elastic API key scope is restricted to migration indices only (`indices` privilege on `migrated-*` or explicit names) and includes the `read_pipeline` cluster privilege if you migrate ingest pipelines
- [ ] API key and IAM credential lifetimes are **at least 2× the expected migration duration** (see [SECURITY.md § Credential rotation](../SECURITY.md))
- [ ] `.env` is **not** committed — confirm with `git status` and `git log --all -- .env`
- [ ] CI/CD pipelines inject credentials from a secret manager (AWS Secrets Manager / Vault); no plain-text secrets in job definitions or task definitions

## 3. Network and TLS **[BLOCKING]**

- [ ] Network topology is recorded ([NETWORK_TOPOLOGY.md](NETWORK_TOPOLOGY.md)). The chosen primary path matches the topology — Path A requires destination → source IP path; Paths D / E / B / F just need compute-side access.
- [ ] All remote cluster URLs use `https://`
- [ ] OpenSearch endpoint is reachable from the host running the migration (or via the [Proxy](../Proxy/README.md))
- [ ] PrivateLink (if used): VPC endpoint accepted, private DNS enabled, `dig` from the compute host returns the **private** IP
- [ ] If using the Proxy: ALB has a valid TLS certificate, security group restricts inbound to known source IPs (or Elastic Cloud egress IPs), `PROXY_USER`/`PROXY_PASSWORD` are set
- [ ] `python preflight.py --strict-exit-codes --source-host ... --dest-host ...` returns green
- [ ] Egress to S3 confirmed if using Path D / E (gateway VPC endpoint preferred to avoid NAT charges)

## 4. Metadata migration **[BLOCKING for Serverless / strict mappings]**

- [ ] `migrate metadata --target-type ELASTICSEARCH_SERVERLESS` (or `ELASTICSEARCH` for self-managed / Hosted) executed and the destination has the expected templates, component templates, composable templates, and ingest pipelines
- [ ] Sanitizer warnings reviewed for legacy `string` types, multi-type mappings, deprecated `_all` / `_timestamp` / `_ttl`
- [ ] Destination index names validated: all lowercase, no leading `-`/`_`/`+`, no spaces or special characters
- [ ] Custom analysers / dictionaries deployed on the destination (these are **not** copied by `migrate metadata`)

See [METADATA_MIGRATION.md](METADATA_MIGRATION.md).

## 5. Pilot run **[BLOCKING]**

- [ ] Pilot index selected (representative but non-critical, e.g. `logs-2024.01.01`)
- [ ] Full migration cycle completed on pilot via the chosen primary path:
  - **Path A:** remote reindex → `poll_reindex_task.py` → `validate_migration.py`
  - **Path B:** Logstash docker-compose run → `validate_migration.py`
  - **Path D:** `migrate s3-extract` → `migrate s3-load` → `validate_migration.py`
  - **Path E:** `migrate rfs` (auto-runs validate)
- [ ] `migrate validate --sample-size 100 --sample-mode stratified` PASS on the pilot
- [ ] `migrate shadow-diff` (curated queries) PASS on the pilot
- [ ] Pilot rollback tested: destination index deleted, primary path re-run successfully (idempotent)

## 6. Destination index preparation

- [ ] Bulk-window settings applied to destination indices: `refresh_interval: -1`, `number_of_replicas: 0` (see [Remote_Reindex/Elastic_destination_index_settings.json](../Remote_Reindex/Elastic_destination_index_settings.json) — **skip these on Serverless, where they are forbidden**)
- [ ] Mapping compatibility verified end-to-end on the pilot (no `mapper_parsing_exception` during pilot bulk)
- [ ] k-NN / vector indices: re-embedding plan in place; source `knn_vector` fields are dropped on ingest and re-embedded by an inference pipeline on the destination (see [SEMANTIC_MIGRATION.md](SEMANTIC_MIGRATION.md))

## 7. Monitoring and alerting

- [ ] Elastic deployment monitoring is enabled and a dashboard is open during cutover
- [ ] OpenSearch `_cluster/health` checked — green before starting
- [ ] Disk headroom confirmed on both clusters (source for reads, destination for writes + replicas after migration)
- [ ] Bulk rejection alerts configured on destination (watch for `429` / threadpool rejections)
- [ ] S3 staging bucket size monitored if using Path D for very large batches (and lifecycle rules in place to clean up post-migration — see step 11)
- [ ] If using Path F (capture/replay): the proxy log volume is monitored (capture mode writes NDJSON to disk or S3)

## 8. Runbook and rollback plan

- [ ] Team has read [RUNBOOK.md](../RUNBOOK.md) and the [org-specific runbook](RUNBOOK_TEMPLATE.md)
- [ ] Rollback procedure documented and tested: point application connection strings back to OpenSearch
- [ ] Maintenance window or dual-write period agreed — no uncoordinated writes during cutover
- [ ] On-call engineer identified and reachable
- [ ] Communication plan agreed (who announces start / completion / issues; via Slack / email / status page)

## 9. Cutover gates **[BLOCKING for read traffic flip]**

- [ ] `migrate validate` PASS across **all** migrated indices, not just the pilot
- [ ] `migrate shadow-diff` PASS with the agreed thresholds (`--count-tolerance`, `--topk-id-threshold`, `--topk-hash-threshold`)
- [ ] (Optional) `migrate replay` against captured production traffic from the previous window — exit 0 or the few drift cases manually classified as acceptable

See [SHADOW_DIFF.md](SHADOW_DIFF.md) and [CAPTURE_REPLAY.md](CAPTURE_REPLAY.md).

## 10. CI checks passing

- [ ] `python3 -m pytest -q` passes on the migration host or in CI
- [ ] `pip-audit -r requirements.txt` shows no critical CVEs (or exceptions are documented)
- [ ] Secret scan (`gitleaks detect` or equivalent) shows no committed secrets
- [ ] Terraform modules used in production (`iac/terraform/rfs-fargate`, `iac/terraform/rfs-orchestration`, `iac/terraform/proxy-ecs`, etc.) have passed `terraform validate` and `tflint`

## 11. Post-migration (after cutover) **[BLOCKING for decommissioning]**

- [ ] `validate_migration.py` re-run on **all** migrated indices — all PASS
- [ ] Application read traffic switched to Elastic; latency and error rate within SLA over the observation window
- [ ] Observation period completed (minimum 24 h recommended before decommissioning OpenSearch)
- [ ] Destination indices restored to production settings: `refresh_interval: 1s`, `number_of_replicas` per policy (skip on Serverless)
- [ ] S3 staging bucket: parts deleted or transitioned to Glacier per data-retention policy; manifest preserved for audit
- [ ] Migration IAM credentials **revoked** (IAM key deleted / role detached)
- [ ] Elastic API key **invalidated** (Stack Management → API keys)
- [ ] Proxy shut down or `PROXY_USER`/`PROXY_PASSWORD` rotated
- [ ] Proxy capture mode disabled (`PROXY_CAPTURE_MODE=off`) and any captured NDJSON archived or deleted per policy
- [ ] Post-migration incident or retrospective ticket created if any issues occurred
- [ ] Migration tooling version recorded in the migration runbook: `migrate --version` and the commit SHA used

## Quick reference: tools by phase

| Phase | Tool | Doc |
|-------|------|-----|
| **Pre-flight** | `migrate compat-check` | [COMPAT_CHECK.md](COMPAT_CHECK.md) |
| **Pre-flight** | `preflight.py` | [AUTOMATION.md](AUTOMATION.md) |
| **Schema** | `migrate metadata` / `migrate sanitize` | [METADATA_MIGRATION.md](METADATA_MIGRATION.md) |
| **Data — A** | `multi_index_reindex.py` / Dev Tools | [Remote_Reindex/README.md](../Remote_Reindex/README.md) |
| **Data — B** | Logstash docker-compose | [Logstash_input/README.md](../Logstash_input/README.md) |
| **Data — D** | `migrate s3-extract` / `migrate s3-load` | [S3_MIGRATION.md](S3_MIGRATION.md) |
| **Data — E** | `migrate rfs` | [RFS.md](RFS.md) |
| **Validation** | `migrate validate` | [README.md](../README.md), [TESTING.md](TESTING.md) |
| **Cutover gate** | `migrate shadow-diff` | [SHADOW_DIFF.md](SHADOW_DIFF.md) |
| **Cutover gate** | `migrate replay` (+ proxy capture) | [CAPTURE_REPLAY.md](CAPTURE_REPLAY.md) |
| **Troubleshooting** | (this checklist + runbook) | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
