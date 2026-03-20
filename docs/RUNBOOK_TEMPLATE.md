# Migration runbook template (fill in for your org)

Copy this into your internal wiki or ticketing space. Replace placeholders in `{{BRACKETS}}`.

## Summary

| Field | Value |
|--------|--------|
| **Initiative** | {{PROJECT_NAME}} |
| **Source** | Amazon OpenSearch Service / {{OTHER}} — `{{SOURCE_ENV}}` |
| **Destination** | Elastic Cloud {{Hosted / Serverless}} — `{{DEST_ENV}}` |
| **Migration window** | {{START}} → {{END}} ({{TIMEZONE}}) |
| **Primary contacts** | {{NAMES / EMAILS}} |

## RACI (example)

| Activity | Responsible | Accountable | Consulted | Informed |
|----------|-------------|-------------|-----------|----------|
| Network / allowlists | | | | |
| Credential issuance & rotation | | | | |
| Reindex / Logstash execution | | | | |
| Validation (`validate_migration.py`) | | | | |
| Cutover & dual-write | | | | |
| Rollback decision | | | | |

## References (repo & internal)

- This toolkit: repo root README, [RUNBOOK.md](../RUNBOOK.md), [SECURITY.md](../SECURITY.md), [docs/SERVERLESS.md](SERVERLESS.md).
- Production & lifecycle: [ORG_PRODUCTION_IAC.md](ORG_PRODUCTION_IAC.md), [TLS_AND_CREDENTIAL_LIFECYCLE.md](TLS_AND_CREDENTIAL_LIFECYCLE.md).
- Internal architecture diagram: {{LINK}}
- Change / incident tickets: {{LINK}}
- On-call / escalation: {{LINK}}

## Pre-flight checklist

- [ ] `reindex.remote.whitelist` (Hosted) or proxy + ALB path verified; [Proxy](../Proxy/README.md) if VPC-only source.
- [ ] Least-privilege IAM / API keys; [SECURITY.md](../SECURITY.md).
- [ ] Index mappings reviewed; conflicts policy documented ([Remote_Reindex](../Remote_Reindex/README.md)).
- [ ] Smoke test on non-prod indices per [docs/TESTING.md](TESTING.md).
- [ ] Async reindex task monitoring: `poll_reindex_task.py` or Kibana task mgmt.

## Cutover & rollback

- **Cutover steps:** {{BULLETS — e.g. stop writers, final sync, DNS, app config}}
- **Rollback:** {{HOW TO REVERT — e.g. keep source read-only until sign-off}}
- **Success criteria:** Count match + optional ID sample (`--check-existence`, `--sample-mode random`).

## Post-migration

- [ ] Archive or delete temporary credentials.
- [ ] Update CMDB / inventory.
- [ ] Retrospective ticket: {{LINK}}
