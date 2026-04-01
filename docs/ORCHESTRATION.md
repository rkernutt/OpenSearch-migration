# Orchestrating migrations (Tines, AWS Step Functions, Jenkins)

Long migrations benefit from a **workflow engine**: retries, human approval, Slack/email on failure, and a single **audit trail**. This repository provides **small CLI tools**; wiring them is your choice of platform.

## What to orchestrate (typical story)

1. **Credential / parameter injection** — source URL, Elastic URL, API keys from a secret store (not hardcoded in the story).
2. **Preflight** — `python3 preflight.py --strict-exit-codes` (optional indices + `--check-counts` after an initial load).
3. **Execute migration** — invoke **Elastic `_reindex`** (HTTP from Tines/Jenkins to your deployment), start **Logstash**/Kafka consumers, or run **curl** with generated JSON.
4. **Poll** — `poll_reindex_task.py --strict-exit-codes` if you use async reindex (add a Step or Action loop with sleep).
5. **Validate** — `validate_migration.py --strict-exit-codes --output-format json` (parse JSON for `summary.failed`).
6. **Notify / ticket** — on non-zero exit or `transport` category, alert and optionally open a case.

Branch **exit code 3** (network/HTTP) for **retry with backoff**; **exit 1** (validation) for **stop** and human triage.

See [AUTOMATION.md](AUTOMATION.md) for exit code tables.

## Tines

[Tines](https://www.tines.com/) fits **API-first** steps: HTTP Request actions to Elasticsearch (trigger `_reindex` with API key), Run Script or Webhook triggers, schedules, and **Stories** with clear success/failure paths.

**Build blueprint (actions, payloads, branching):** [TINES_STORY_TEMPLATE.md](TINES_STORY_TEMPLATE.md) and [examples/tines/README.md](../examples/tines/README.md).

**Suggested pattern:**

- **Secrets:** Tines **Credentials** or **Global Resources** for Elastic API key, OpenSearch basic auth, AWS keys if you call SigV4 from Tines (less common; often preflight runs on a runner with IAM).
- **Actions:** HTTP Request to `POST /_reindex` on Elastic (body from a **Text** action or fetched **Object**); second story branch Polls `GET _tasks/<id>` until complete or use **Event Transformation** + **Delay** + loop metadata.
- **Wrapping CLIs:** If Tines agents run on a host with this repo checked out, use **Execute Command** (where permitted): `make preflight ARGS='...'` / `make validate ARGS='...'` and parse stdout.
- **Error handling:** Map shell exit codes **3** → **Retry** story or **Counter**; **1** → **Send Email/Slack** + optional **Human in the loop**.

Tines does not need a custom export in this repo: build stories against your endpoints using the same environment variables as [`.env.example`](../.env.example).

## AWS Step Functions

Use when everything runs **in AWS**: **Lambda** or **ECS/Fargate** tasks execute shell/Python, state is in **Step Functions**, secrets in **Secrets Manager** / **Parameter Store**.

**Suggested pattern:**

- **Task 1:** Run container or Lambda with `preflight.py --strict-exit-codes` (package repo or minimal image).
- **Task 2:** Lambda **HTTPS** to Elastic `_reindex` (or trigger your existing automation).
- **Choice:** On API response, branch to **PollTask** Map or Wait + `_tasks` GET until complete.
- **Task 3:** `validate_migration.py --strict-exit-codes --output-format json`.
- **Retry:** Step Functions `Retry` on **Lambda.TaskFailed** when exit code maps to **States.TaskFailed** (Lambda must translate exit 3 to a retriable error or use a wrapper script).

**IaC:** optional: a minimal **SAM** or **CDK** stack is out of scope for this repo; keep Step Function ASL in your org’s git.

## Jenkins

Classic **Freestyle** or **Pipeline** (`Jenkinsfile`): checkout repo, `withCredentials` for Elastic/API keys, sh `make preflight`, sh migration step, sh `make validate`.

**Suggested pattern:**

- **Pipeline stages:** Preflight → Migrate (manual `input` for production if needed) → Validate → Archive **JSON** artifacts from `--output-format json`.
- **Retry:** `retry(3) { sh ... }` only for steps you classify as **transient**; narrow the scope so validation failures do not retry blindly.

## GitHub Actions / GitLab CI

For **repository hygiene** (pytest on push/PR), this repo uses [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). For **production migration**, avoid storing long-lived cluster secrets in CI unless you use **OIDC** + short-lived tokens and a locked-down workflow.

## Choosing a tool

| Platform | Strengths |
|----------|-----------|
| **Tines** | Fast to build API chains, human steps, notifications, secret handling |
| **Step Functions** | Native AWS, long-running with Wait, fits VPC-only workloads |
| **Jenkins** | Familiar to enterprise ops, scripted pipelines, on-prem agents |

All of them can call the same **`make`/`python3`** commands documented in [AUTOMATION.md](AUTOMATION.md).
