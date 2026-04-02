# Production deployment checklist

Work through this list before committing to the production cutover window.
Items marked **[BLOCKING]** must be complete before migrating production traffic.

---

## 1. Credentials and access **[BLOCKING]**

- [ ] Migration IAM role / user is **separate** from the production search role — minimum permissions per [SECURITY.md](../SECURITY.md)
- [ ] Elastic API key scope is restricted to migration indices only (`indices` privilege on `migrated-*` or explicit names)
- [ ] API key and IAM credential lifetimes are **at least 2× the expected migration duration** (see [SECURITY.md § Credential rotation](../SECURITY.md))
- [ ] `.env` is **not** committed — confirm with `git status` and `git log --all -- .env`
- [ ] CI/CD pipelines inject credentials from a secret manager; no plain-text secrets in job definitions

## 2. Network and TLS **[BLOCKING]**

- [ ] All remote cluster URLs use `https://`
- [ ] OpenSearch endpoint is reachable from the host running the migration (or via the [Proxy](../Proxy/README.md))
- [ ] If using the Proxy: ALB has a valid TLS certificate, security group restricts inbound to known source IPs (or Elastic Cloud egress IPs), `PROXY_USER`/`PROXY_PASSWORD` are set
- [ ] `python preflight.py --source-host ... --dest-host ...` returns all green

## 3. Pilot run **[BLOCKING]**

- [ ] Pilot index selected (representative but non-critical, e.g. `logs-2024.01.01`)
- [ ] Full migration cycle completed on pilot: reindex → `validate_migration.py` → confirm counts match
- [ ] `--sample-size` used during validation (e.g. `--sample-size 100 --sample-mode stratified`)
- [ ] Pilot rollback tested: destination index deleted, reindex re-run successfully

## 4. Destination index preparation

- [ ] Destination indices pre-created with `refresh_interval: -1` and `number_of_replicas: 0` for bulk load (see [Remote_Reindex/Elastic_destination_index_settings.json](../Remote_Reindex/Elastic_destination_index_settings.json))
- [ ] Mapping compatibility verified: no type conflicts between source and destination
- [ ] Index names validated: all lowercase, no leading `-`/`_`/`+`, no spaces or special characters

## 5. Monitoring and alerting

- [ ] Elastic deployment monitoring is enabled and a dashboard is open during cutover
- [ ] OpenSearch `_cluster/health` checked — green before starting
- [ ] Disk headroom confirmed on both clusters (source for reads, destination for writes + replicas after migration)
- [ ] Bulk rejection alerts configured on destination (watch for `429` / threadpool rejections)

## 6. Runbook and rollback plan

- [ ] Team has read [RUNBOOK.md](../RUNBOOK.md) and the [org-specific runbook](RUNBOOK_TEMPLATE.md)
- [ ] Rollback procedure documented and tested: point application connection strings back to OpenSearch
- [ ] Maintenance window or dual-write period agreed — no uncoordinated writes during cutover
- [ ] On-call engineer identified and reachable

## 7. CI checks passing

- [ ] `python3 -m pytest -q` passes on the migration host or in CI
- [ ] `pip-audit -r requirements.txt` shows no critical CVEs (or exceptions are documented)
- [ ] Secret scan (`gitleaks detect` or equivalent) shows no committed secrets

## 8. Post-migration (after cutover)

- [ ] `validate_migration.py` run on **all** migrated indices — all PASS
- [ ] Application read traffic switched to Elastic; latency and error rate within SLA
- [ ] Observation period completed (minimum 24 h recommended before decommissioning OpenSearch)
- [ ] Migration IAM credentials **revoked** (IAM key deleted / role detached)
- [ ] Elastic API key **invalidated** (Stack Management → API keys)
- [ ] Proxy shut down or `PROXY_USER`/`PROXY_PASSWORD` rotated
- [ ] Destination indices restored to production settings: `refresh_interval: 1s`, `number_of_replicas` per policy
- [ ] Post-migration incident or retrospective ticket created if any issues occurred
