# Automation and exit codes

This project is aimed at **CLI and pipeline** operators. Use **repo-root `.env`** for secrets (see [GETTING_STARTED.md](GETTING_STARTED.md)) and prefer **`make`** or direct `python3 …` invocations from CI.

## Make targets

From the repository root ([Makefile](../Makefile)):

| Target | Script | Typical use |
|--------|--------|-------------|
| `make test` | `pytest` | PR checks, no cluster |
| `make preflight ARGS="..."` | [preflight.py](../preflight.py) | Before long reindex/Logstash |
| `make validate ARGS="..."` | [validate_migration.py](../validate_migration.py) | Post-sync verification |
| `make poll-task ARGS="..."` | [poll_reindex_task.py](../poll_reindex_task.py) | Async `_reindex` task |
| `make reindex-gen ARGS="..."` | [multi_index_reindex.py](../multi_index_reindex.py) | Emit Dev Tools bodies (passwords from env) |

Example:

```bash
make preflight ARGS='--strict-exit-codes --source-index my-index --dest-index my-index'
make validate ARGS='--strict-exit-codes --output-format json --sample-size 10 --source-index my-index --dest-index my-index'
```

## `validate_migration.py` exit codes

**Default** (no flag):

| Code | Meaning |
|------|---------|
| 0 | All index pairs passed |
| 1 | Validation or configuration error (including missing boto3 / Elastic auth in early checks) |
| 2 | Argparse usage error |

**With `--strict-exit-codes`:**

| Code | Meaning |
|------|---------|
| 0 | All pairs passed |
| 1 | Data validation failed (count mismatch, missing index when checking, `_mget` sample miss, etc.) |
| 2 | Misconfiguration (missing hosts, missing Elastic auth, empty batch, missing boto3 when SigV4 needed) |
| 3 | Network / HTTP failure talking to OpenSearch or Elastic (`requests` errors) |

Use **3** to drive **retry/backoff** in orchestration; use **1** to **stop and investigate** migration logic.

JSON and CSV output include a per-row **`category`** field: `ok`, `validation`, or `transport`.

## `poll_reindex_task.py` exit codes

**With `--strict-exit-codes`:**

| Code | Meaning |
|------|---------|
| 0 | Task completed successfully |
| 1 | Task completed with Elasticsearch error or shard failures |
| 2 | Wait timeout (or argparse usage) |
| 3 | HTTP / network failure while polling `_tasks` |

Without the flag, network errors exit **1** (same as task failure); timeout stays **2**.

## `preflight.py` exit codes

**With `--strict-exit-codes`:**

| Code | Meaning |
|------|---------|
| 0 | Checks passed |
| 1 | Logical failure (e.g. count mismatch with `--check-counts`, missing index) |
| 2 | Missing required flags/env |
| 3 | HTTP / network error on ping or HEAD |

Without `--strict-exit-codes`, any failure exits **1** (except argparse, exit **2**).

## `multi_index_reindex.py` and `.env`

The generator reads **`SOURCE_OPENSEARCH_HOST`**, **`SOURCE_OPENSEARCH_USER`**, **`SOURCE_OPENSEARCH_PASSWORD`**, **`MIGRATION_DEST_PREFIX`**, **`MIGRATION_DEST_SUFFIX`** when flags are omitted, so remote credentials are less likely to appear in shell history. Reindex bodies still contain `username`/`password` JSON fields—**generate in a secure workspace** and prefer **short-lived migration users**.

## Further reading

- [ORCHESTRATION.md](ORCHESTRATION.md) — Tines, Step Functions, Jenkins
- [TESTING.md](TESTING.md) — pytest and integration
