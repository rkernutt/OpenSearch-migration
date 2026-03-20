# Security

This document describes how to handle secrets and **least-privilege** access for this migration project.

## Secrets and configuration

- **Do not commit** real endpoints, passwords, API keys, or ARNs. Use a local `.env` file (see [.env.example](.env.example)). `.env` is listed in [.gitignore](.gitignore). Also ignore patterns cover `*.secret`, `credentials.json`, and `secrets/`.
- **`.env.example` is safe to commit:** it contains placeholders only. Never paste production values into tracked files.
- **Install optional dotenv support:** `pip install -r requirements.txt` includes `python-dotenv`. These tools load repo-root `.env` via [bootstrap_env.py](bootstrap_env.py) when present:
  - [validate_migration.py](validate_migration.py)
  - [poll_reindex_task.py](poll_reindex_task.py)
  - [Proxy/app.py](Proxy/app.py) (when run from the repo)
- **CI/CD:** Inject secrets from your secret manager (AWS Secrets Manager, GitHub Actions secrets, etc.) as environment variables—never echo them in logs or store them in job artifacts.
- **Separate credentials for migration:** Use a dedicated IAM user/role and Elastic API key (or user) for migration only. Rotate or revoke them after cutover. Do not reuse production search credentials.
- **Git history:** If a secret was ever committed, rotate it everywhere and consider `git filter-repo` or platform-specific secret scanning; use tools such as **gitleaks** or **TruffleHog** in CI on pull requests. This repo ships [`.github/workflows/ci.yml`](.github/workflows/ci.yml) with **pytest**, non-blocking **pip-audit**, and non-blocking **gitleaks**—tighten or remove `continue-on-error` per your policy. For **blocking** scans on a cadence or manual run, use [`.github/workflows/ci-security-strict.yml`](.github/workflows/ci-security-strict.yml).
- **TLS:** Use `https://` endpoints for OpenSearch, Elastic, and public proxies. Do not accept downgraded or misverified TLS in production migration paths. For certificate, key, and allowlist **rotation and reviews**, see [docs/TLS_AND_CREDENTIAL_LIFECYCLE.md](docs/TLS_AND_CREDENTIAL_LIFECYCLE.md) and [docs/ORG_PRODUCTION_IAC.md](docs/ORG_PRODUCTION_IAC.md).

### Environment variables reference (sensitive)

| Variable(s) | Used by |
|-------------|---------|
| `SOURCE_*`, `DEST_*`, `AWS_*` | `validate_migration.py` |
| `DEST_ELASTIC_*` | `poll_reindex_task.py` |
| `OPENSEARCH_ENDPOINT`, `PROXY_USER`, `PROXY_PASSWORD`, `AWS_*` | [Proxy/app.py](Proxy/app.py) |

---

## IAM: who needs what

### 1. Your workstation or automation calling OpenSearch APIs (SigV4)

Used by: `validate_migration.py` (when using SigV4 for the source), and optionally the [Proxy](Proxy/README.md).

**Minimum (typical):** allow HTTP calls to the OpenSearch domain (read operations such as `_count`, `_search` for validation; the proxy forwards whatever your client sends).

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "es:ESHttpGet",
        "es:ESHttpHead",
        "es:ESHttpPost",
        "es:ESHttpPut",
        "es:ESHttpDelete"
      ],
      "Resource": "arn:aws:es:REGION:ACCOUNT_ID:domain/YOUR_DOMAIN_NAME/*"
    }
  ]
}
```

Narrow `Action` if you know the exact operations your use case needs (for example read-only for validation only).

### 2. Proxy ([Proxy/app.py](Proxy/app.py))

The task/instance role should allow `es:ESHttpGet`, `es:ESHttpPost`, `es:ESHttpPut`, `es:ESHttpHead`, `es:ESHttpDelete` on the target domain (same pattern as section 1).

**Logging:** Avoid logging full request or response bodies on the proxy; they can contain PII or credentials in queries. The stock proxy does not log bodies.

### 3. Elastic Cloud API key (destination)

Create an API key (or user) **only for migration**:

- **Indices:** `read`/`write` (or `create_index`) on the destination indices you migrate into—not cluster admin.
- **`poll_reindex_task.py` needs** `read` access sufficient for `GET _tasks/<id>` (typically `cluster:monitor` / task APIs as allowed by your role).
- **Avoid** using `superuser` or keys with `manage_security` for routine migration.
- After migration, **delete or disable** the key.

Elastic’s UI lets you restrict an API key by role; define a custom role with `indices` privileges on `migrated-*` or explicit index names.

---

## OpenSearch fine-grained access

If the domain uses fine-grained access, map the IAM principal or internal user to a role that can perform only the needed cluster/index actions (for example read indices for migration and validation).

---

## Proxy basic auth (`PROXY_USER` / `PROXY_PASSWORD`)

When the proxy is exposed publicly (e.g. behind an ALB), always set strong random credentials and rotate them if exposed. Prefer network restrictions (security groups) in addition to basic auth.

---

## Dependencies

- Pin versions in [requirements.txt](requirements.txt) and refresh periodically.
- In CI or locally: consider **`pip audit`** (or your org’s SCA tool) on `requirements.txt` / `requirements-dev.txt` to catch known vulnerable packages.

---

## Checklist

- [ ] `.env` is git-ignored and not committed.
- [ ] Migration IAM and Elastic API key are separate from production.
- [ ] Domain policy and IAM policies use specific ARNs, not `*`.
- [ ] Revoke migration credentials after go-live.
- [ ] Pre-commit or CI scans for accidental secrets (optional but recommended).
- [ ] TLS in use for all remote cluster URLs in automation.
