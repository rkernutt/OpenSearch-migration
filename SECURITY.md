# Security

This document describes how to handle secrets and **least-privilege** access for this migration project.

## Secrets and configuration

- **Do not commit** real endpoints, passwords, API keys, or ARNs. Use a local `.env` file (see [.env.example](.env.example)). `.env` is listed in [.gitignore](.gitignore). Also ignore patterns cover `*.secret`, `credentials.json`, and `secrets/`.
- **`.env.example` is safe to commit:** it contains placeholders only. The same applies to [examples/env/*.env.example](examples/env/) templates. Never paste production values into tracked files; copy them to repo-root `.env` locally only.
- **Install optional dotenv support:** `pip install -r requirements.txt` includes `python-dotenv`. These tools load repo-root `.env` via [bootstrap_env.py](bootstrap_env.py) when present:
  - [validate_migration.py](validate_migration.py)
  - [preflight.py](preflight.py)
  - [multi_index_reindex.py](multi_index_reindex.py)
  - [poll_reindex_task.py](poll_reindex_task.py)
  - [Proxy/app.py](Proxy/app.py) (when run from the repo)
- **CI/CD:** Inject secrets from your secret manager (AWS Secrets Manager, GitHub Actions secrets, etc.) as environment variables—never echo them in logs or store them in job artifacts.
- **Separate credentials for migration:** Use a dedicated IAM user/role and Elastic API key (or user) for migration only. Rotate or revoke them after cutover. Do not reuse production search credentials.
- **Git history:** If a secret was ever committed, rotate it everywhere and consider `git filter-repo` or platform-specific secret scanning; use tools such as **gitleaks** or **TruffleHog** in CI on pull requests. This repo ships [`.github/workflows/ci.yml`](.github/workflows/ci.yml) with **pytest**, non-blocking **pip-audit**, and non-blocking **gitleaks**—tighten or remove `continue-on-error` per your policy. For **blocking** scans on a cadence or manual run, use [`.github/workflows/ci-security-strict.yml`](.github/workflows/ci-security-strict.yml).
- **TLS:** Use `https://` endpoints for OpenSearch, Elastic, and public proxies. Do not accept downgraded or misverified TLS in production migration paths. For certificate, key, and allowlist **rotation and reviews**, see [docs/TLS_AND_CREDENTIAL_LIFECYCLE.md](docs/TLS_AND_CREDENTIAL_LIFECYCLE.md) and [docs/ORG_PRODUCTION_IAC.md](docs/ORG_PRODUCTION_IAC.md).

### Environment variables reference (sensitive)

| Variable(s) | Used by |
|-------------|---------|
| `SOURCE_*`, `DEST_*`, `AWS_*` | `validate_migration.py`, `preflight.py` |
| `DEST_ELASTIC_*` | `poll_reindex_task.py`, `preflight.py` |
| `DEST_ELASTIC_API_KEY_ENCODED` | `validate_migration.py`, `poll_reindex_task.py` — set to `1` if the API key is already Base64-encoded |
| `MIGRATION_DEST_*` | `multi_index_reindex.py` (optional defaults) |
| `OPENSEARCH_ENDPOINT`, `PROXY_USER`, `PROXY_PASSWORD`, `AWS_*` | [Proxy/app.py](Proxy/app.py) |
| `PROXY_VERIFY_TLS`, `PROXY_CA_BUNDLE`, `PROXY_DEBUG` | [Proxy/app.py](Proxy/app.py) — TLS and debug options |
| `VALIDATION_TIMEOUT_SHORT`, `VALIDATION_TIMEOUT_SEARCH` | `validate_migration.py`, `preflight.py` — override default request timeouts |

---

## IAM: who needs what

### 1. `validate_migration.py` and `preflight.py` (SigV4 source auth)

When using SigV4 (no `--source-user` / `--source-password`), the caller's IAM principal needs **read-only** access to the source domain. These scripts only use `GET`, `HEAD`, and `POST` (read operations: `_count`, `_search`, `_mget`).

**Minimum least-privilege policy:**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "es:ESHttpGet",
        "es:ESHttpHead",
        "es:ESHttpPost"
      ],
      "Resource": "arn:aws:es:REGION:ACCOUNT_ID:domain/YOUR_DOMAIN_NAME/*"
    }
  ]
}
```

Do **not** grant `es:ESHttpPut`, `es:ESHttpDelete`, or `es:ESHttpPatch` to the validation role — it only reads.

### 2. Your workstation or automation calling OpenSearch APIs for reindex (SigV4)

Used by: the [Proxy](Proxy/README.md) when forwarding reindex or write operations.

**Broader policy (reindex or write path):**

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

Narrow `Action` if you know the exact operations your use case needs.

### 3. Proxy ([Proxy/app.py](Proxy/app.py))

The task/instance role should allow `es:ESHttpGet`, `es:ESHttpPost`, `es:ESHttpPut`, `es:ESHttpHead`, `es:ESHttpDelete` on the target domain (same pattern as section 1).

**Logging:** Avoid logging full request or response bodies on the proxy; they can contain PII or credentials in queries. The stock proxy does not log bodies.

### 4. Elastic Cloud API key (destination)

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

## Credential rotation and long-running jobs

Migration jobs can run for hours. Plan credential lifetimes accordingly.

### Before starting a long job

- **Elastic API keys:** create keys with an expiration **at least 2× the expected migration duration** plus a buffer (e.g. 24 h for a 6 h job). Check the expiry in the Elastic UI under Stack Management → API keys.
- **AWS temporary credentials (AssumeRole / instance metadata):** the default session duration is 1 h (configurable up to 12 h for roles). If `poll_reindex_task.py` or `validate_migration.py` runs longer than the session, the next AWS SDK call refreshes credentials automatically via the credential chain — no action needed as long as the IAM role is still attached and the instance metadata service is reachable.
- **IAM user long-term keys:** avoid for automation; prefer instance/task roles or short-lived tokens from `sts:AssumeRole`.

### If a key expires mid-migration

`poll_reindex_task.py` (≥ v1.0) tolerates up to 5 consecutive HTTP failures before aborting. A 401 on each poll attempt will exhaust those retries within seconds.

**Recovery steps:**

1. Note the `task_id` from the original `POST _reindex?wait_for_completion=false` response — you can always re-attach to a running task.
2. Create a new Elastic API key or rotate the existing one.
3. Re-run `poll_reindex_task.py` with the new key: `--dest-api-key <new_key>`.
4. The reindex task continues in the background on Elastic regardless of whether the poller is running.

### After cutover

Revoke all migration-specific credentials immediately after the observation period:

- **Elastic:** Stack Management → API keys → Invalidate.
- **AWS:** delete the IAM user key pair or remove the migration role from the instance/task profile.
- **Proxy basic auth:** change `PROXY_USER`/`PROXY_PASSWORD` or tear down the proxy ECS service / EC2 instance.

---

## Checklist

- [ ] `.env` is git-ignored and not committed.
- [ ] Migration IAM and Elastic API key are separate from production.
- [ ] Domain policy and IAM policies use specific ARNs, not `*`.
- [ ] Revoke migration credentials after go-live.
- [ ] Pre-commit or CI scans for accidental secrets (optional but recommended).
- [ ] TLS in use for all remote cluster URLs in automation.
