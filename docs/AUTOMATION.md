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
| `make s3-extract ARGS="..."` | [s3_migration/s3_extract.py](../s3_migration/s3_extract.py) | Extract OpenSearch indices to gzipped NDJSON in S3 |
| `make s3-load ARGS="..."` | [s3_migration/s3_bulk_load.py](../s3_migration/s3_bulk_load.py) | Bulk-load gzipped NDJSON parts from S3 into Elastic |
| `make rfs ARGS="..."` | [s3_migration/rfs_runner.py](../s3_migration/rfs_runner.py) | Run upstream Reindex-from-Snapshot in a container and validate |
| `make metadata ARGS="..."` | [metadata_migration/migrator.py](../metadata_migration/migrator.py) | Migrate templates / component templates / ingest pipelines with sanitization |
| `make sanitize ARGS="..."` | [metadata_migration/sanitizer.py](../metadata_migration/sanitizer.py) | One-off settings/mapping sanitization (stdin/stdout JSON) |
| `make migrate ARGS="<subcommand> ..."` | [migrate.py](../migrate.py) | Umbrella CLI: `migrate preflight`, `migrate s3-load`, `migrate metadata`, … |
| `make shadow-diff ARGS="..."` | [shadow_diff.py](../shadow_diff.py) | Replay saved queries against source + dest; drift exits non-zero |
| `make replay ARGS="..."` | [replay/replayer.py](../replay/replayer.py) | Replay captured proxy traffic (Path F) against the destination |

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

## `s3_migration/s3_extract.py` exit codes

**With `--strict-exit-codes`:**

| Code | Meaning |
|------|---------|
| 0 | Every requested index extracted; manifest written |
| 2 | Configuration error (missing flags, invalid `s3://` URI, bad `--query-file`) |
| 3 | Transport / auth failure talking to S3 or OpenSearch (during client setup or the final manifest write) |
| 4 | One or more indices failed mid-extraction; the manifest still reflects what completed and a rerun resumes from the checkpoint |

The final stdout line in `--log-format json` is a single summary object with `indices_total`, `indices_failed`, `documents_extracted`, `parts_total`, `elapsed_seconds`, `manifest_uri`, and `dry_run`.

## `s3_migration/s3_bulk_load.py` exit codes

**With `--strict-exit-codes`:**

| Code | Meaning |
|------|---------|
| 0 | All parts loaded; no document-level failures |
| 2 | Configuration error (missing flags, invalid `s3://` URI, unreadable manifest) |
| 3 | Transport / auth failure talking to S3 or Elasticsearch |
| 4 | At least one document-level failure (written to the DLQ when enabled, or aborted the part when `--no-dlq` is set); load otherwise completed |

Without `--strict-exit-codes`, the loader returns **0** when DLQ handled all per-document failures, and **1** for any other failure (including config and transport).

The S3 loader logs structured JSON when invoked with `--log-format json`; the final stdout line is a single summary object containing `parts_total`, `parts_completed`, `documents_succeeded`, `documents_failed`, `bytes_posted`, `failed_parts`, `dlq_used`, and `dry_run`.

## `s3_migration/rfs_runner.py` exit codes

**With `--strict-exit-codes`:**

| Code | Meaning |
|------|---------|
| 0 | RFS completed; post-run validation (when requested) succeeded |
| 2 | Configuration error (missing `--upstream-image`, container runtime not on PATH, missing target credentials) |
| 3 | RFS process exited non-zero, or post-run validation hit a transport error |
| 4 | RFS completed but `validate_migration.py` reported count / sampling mismatches |

The wrapper streams the upstream container's stdout/stderr through the shared logger; secrets are passed in via container env vars and never appear on the command line.

## `metadata_migration/migrator.py` exit codes

**With `--strict-exit-codes`:**

| Code | Meaning |
|------|---------|
| 0 | Every selected object copied (or already present) |
| 2 | Configuration error (missing flags, unknown `--include` kind, missing auth) |
| 3 | Transport / auth failure listing objects on the source |
| 4 | One or more objects failed sanitization or PUT (others may have succeeded; rerun is safe — destination PUTs are idempotent) |

The summary line on stdout (`--log-format json`) is a single object containing `by_status`, `failed`, `elapsed_seconds`, and `dry_run`.

## `shadow_diff.py` exit codes

**With `--strict-exit-codes`:**

| Code | Meaning |
|------|---------|
| 0 | Every query within tolerance |
| 2 | Configuration error (missing flags, no queries, bad JSON) |
| 3 | Every query failed transport (suggests a global auth / network issue) |
| 4 | At least one query drifted beyond tolerance (others may have passed; the report enumerates) |

The summary line on stdout (`--log-format json`) contains `queries_total`, `queries_drifted`, `transport_failures`, `elapsed_seconds`, and a `drift_by_name` array of `{name, index, failures, metrics, detail}` entries.

## `replay/replayer.py` exit codes

**With `--strict-exit-codes`:**

| Code | Meaning |
|------|---------|
| 0 | Every replayed request matched comparator thresholds |
| 2 | Configuration error (missing flags, no captures path, bad ISO timestamp) |
| 3 | Every replayed request errored (suggests a global auth / network issue) |
| 4 | One or more comparators drifted (others may have passed; report enumerates) |

The summary line on stdout (`--log-format json`) contains `requests_total`, `requests_passed`, `requests_drifted`, `transport_failures`, `matched`, `seen`, `elapsed_seconds`, and a `drift_sample` (first 50 drifts).

## `metadata_migration/sanitizer.py` exit codes

| Code | Meaning |
|------|---------|
| 0 | Sanitization completed (or, with `--strict`, no changes were made) |
| 1 | Sanitization completed *but* changes were made and `--strict` was set |
| 2 | Argparse usage error |

## `multi_index_reindex.py` and `.env`

The generator reads **`SOURCE_OPENSEARCH_HOST`**, **`SOURCE_OPENSEARCH_USER`**, **`SOURCE_OPENSEARCH_PASSWORD`**, **`MIGRATION_DEST_PREFIX`**, **`MIGRATION_DEST_SUFFIX`** when flags are omitted, so remote credentials are less likely to appear in shell history. Reindex bodies still contain `username`/`password` JSON fields—**generate in a secure workspace** and prefer **short-lived migration users**.

## Further reading

- [ORCHESTRATION.md](ORCHESTRATION.md) — Tines, Step Functions, Jenkins
- [TESTING.md](TESTING.md) — pytest and integration
