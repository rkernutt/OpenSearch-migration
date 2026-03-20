# TLS, certificates, and credential lifecycle

Use this as an **org checklist** for the OpenSearch → Elastic paths in this repo (remote reindex, Logstash, proxy). Copy sections into your CMDB or change calendar.

## TLS / transport

| Asset | What to verify | Typical owner |
|-------|----------------|---------------|
| **ACM certificate** on ALB | Valid SAN covers public hostname; renewal via DNS | Cloud / DNS |
| **ALB SSL policy** | Meets org cipher/TLS minimum (e.g. TLS 1.2+) | Security / Cloud |
| **OpenSearch domain** | `Node-to-node encryption`, `HTTPS` client endpoint policy | Data platform |
| **Elastic Cloud** | Hosted/Serverless endpoints use Elastic-managed TLS; no custom cert on your side for managed URL | N/A |
| **Logstash / proxy containers** | Trust store for outbound `https://` to OpenSearch and Elastic | Platform |

**Review cadence:** at least **annual**, or when frameworks change (e.g. new PCI/ISO guidance).

## Credentials and API keys

| Credential | Used for | Rotation hints |
|------------|----------|----------------|
| **Elastic API key** (`DEST_ELASTIC_API_KEY`) | `validate_migration.py`, `poll_reindex_task.py`, reindex on Elastic | Short-lived keys in CI; rotate after migration; restrict roles to needed indices |
| **OpenSearch FGAC user/password** | Remote reindex `source.remote`, Logstash, proxy basic auth consumers | Rotate on schedule; disable unused users |
| **Proxy basic auth** (`PROXY_USER` / `PROXY_PASSWORD`) | Clients (Elastic) hitting the proxy | Store in Secrets Manager; rotate with Elastic allowlist/credential updates coordinated |
| **IAM (SigV4)** | OpenSearch when using IAM principal; ECS task role for proxy | Narrow policy to domain ARN; audit via CloudTrail |
| **Cloud API keys** (Elastic Cloud admin) | Provisioning only—not for data plane migration | Separate from data keys; MFA + break-glass |

## Elastic Cloud: `reindex.remote.whitelist`

- Treat allowlist edits as **production changes**: ticket, peer review, apply in maintenance window if needed.
- When the proxy hostname or ALB changes, update **whitelist** and **remote credentials** together.
- After rotation, run a **smoke reindex** or `validate_migration.py` on a tiny index (see [TESTING.md](TESTING.md)).

## OpenSearch access policies

- **IP / VPC** restrictions: if Elastic egress IPs change (documented ranges vary by product), update domain policy or security groups.
- **Fine-grained access:** migration users should have **minimum** index privileges; remove after cutover.

## Incident playbooks (short)

1. **Suspected leaked API key or password:** revoke in Elastic/OpenSearch immediately; rotate; check audit logs for unusual `_search` / `_bulk` / `/_cat`.
2. **Certificate expiration warning:** ACM usually auto-renews; fix DNS validation failures first.
3. **TLS handshake errors** after policy change: compare client TLS version/ciphers with ALB policy and OpenSearch minimum TLS.

## RACI placeholder

| Task | Responsible | Accountable |
|------|-------------|-------------|
| Quarterly access review (migration principals) | | |
| Elastic API key issuance/rotation | | |
| OpenSearch FGAC + IAM review | | |
| ALB/WAF rule changes | | |

---

**Related:** [SECURITY.md](../SECURITY.md), [ORG_PRODUCTION_IAC.md](ORG_PRODUCTION_IAC.md), [RUNBOOK_TEMPLATE.md](RUNBOOK_TEMPLATE.md).
