# Testing and verification

Use these steps to verify connectivity, credentials, and tooling **before** migrating production data.

## Prerequisites

- Python **3.9+** recommended (`from __future__ import annotations` and typing used in some scripts).
- From the repo root: `pip install -r requirements.txt`
- For automated checks in CI: `pip install -r requirements-dev.txt && pytest`

## Quick CI-style check (no cluster required)

Runs `--help` on the CLI tools to ensure imports and entrypoints work:

```bash
python3 -m pytest -q
```

See [tests/test_cli_help.py](../tests/test_cli_help.py).

**Offline unit tests (no cluster):**

- [tests/test_sample_search_body.py](../tests/test_sample_search_body.py) — `head` vs `random` sampling query bodies.
- [tests/test_verify_mget_mock.py](../tests/test_verify_mget_mock.py) — `_mget` parsing with mocked HTTP.

## `validate_migration.py` output and sampling

For dashboards or CI artifacts:

```bash
python validate_migration.py --indices "a,b" --output-format json
python validate_migration.py --indices "a,b" --output-format csv
```

For broader ID coverage than deterministic “first N by `_doc`”, use random scoring (same index version; re-run after major merges if you need a new random draw):

```bash
python validate_migration.py --source-index src --dest-index dest \
  --check-existence --sample-size 50 --sample-mode random --random-seed 12345
```

`--sample-mode head` (default) keeps the previous `_doc` ordering.

**Stratified sampling** (Elasticsearch / OpenSearch **sliced search** + `random_score`) spreads samples across logical slices—useful on very large indices. Requires cluster support for the `slice` parameter on `_search` (Elasticsearch 2.1+ / modern OpenSearch).

```bash
python validate_migration.py --source-index src --dest-index dest \
  --check-existence --sample-size 100 --sample-mode stratified --sample-slices 12 --random-seed 7
```

If `--sample-slices` is omitted, slice count defaults to `min(8, sample size)`.

**Time / value stratified sampling** uses a `stats` aggregation **min/max** on a field (typically `@timestamp` as **epoch millis**, or another **numeric** field), splits that range into buckets, then random-samples within each bucket:

```bash
python validate_migration.py --source-index logs --dest-index logs \
  --check-existence --sample-size 60 --sample-mode time_stratified \
  --time-field @timestamp --time-buckets 10 --random-seed 1
```

If the field is unmapped, wrong type, or all values are missing, validation fails with a clear message. For keyword-only time strings, map a **numeric** runtime field or use `stratified` / `random` instead.

## Smoke test A: Validate migration tooling only

If you already have a **non-production** source index on OpenSearch and a destination index on Elastic with matching document counts:

```bash
export SOURCE_OPENSEARCH_HOST="https://search-....es.amazonaws.com"
export DEST_ELASTIC_HOST="https://....found.io"
export DEST_ELASTIC_API_KEY="..."   # or user/password

python validate_migration.py \
  --source-index test-smoke-src \
  --dest-index test-smoke-dest \
  --check-existence \
  --sample-size 5
```

- Expect **`PASS`** and **`ID sample OK`** if data was copied correctly.
- Adjust index names; use `--source-user` / `--source-password` if the source uses basic auth instead of SigV4.

## Smoke test B: Remote reindex path (Elastic Hosted)

1. On **OpenSearch**, create a tiny test index (e.g. `migration-smoke-test`) with a few documents (Dev Tools or `_bulk`).
2. On **Elastic Cloud Hosted**, set `reindex.remote.whitelist` to your OpenSearch host:443.
3. In Elastic Dev Tools, run `_reindex` from [Remote_Reindex/Elastic_DEVTOOLS_reindex.json](../Remote_Reindex/Elastic_DEVTOOLS_reindex.json) (or a generated block from `multi_index_reindex.py`) pointing at that index; destination index name e.g. `migration-smoke-test-dest`.
4. Run **Smoke test A** against the source and destination names.
5. Delete the test indices when finished.

This confirms allowlist, remote credentials, network path, and validation scripts.

## Smoke test C: Logstash path

1. At the repo root, copy [.env.example](../.env.example) to `.env` and set `SOURCE_OPENSEARCH_*`, `LOGSTASH_SOURCE_INDEX`, `LOGSTASH_DEST_INDEX`, and `ELASTIC_CLOUD_ID` + `ELASTIC_CLOUD_AUTH` (or use compose profile `apikey` with `DEST_ELASTIC_*`). Use a **small test** index on both sides.
2. From [Logstash_input](../Logstash_input): `docker compose up --build` (see [Logstash_input/README.md](../Logstash_input/README.md)).
3. Confirm documents appear in the Elastic destination index.
4. Run `validate_migration.py` with `--sample-size` on that index pair.

Alternative (manual config only): copy [sample_logstash.conf](../Logstash_input/sample_logstash.conf) and build with [sample_Dockerfile](../Logstash_input/sample_Dockerfile).

## Async reindex + poll

After `POST _reindex?wait_for_completion=false`, capture the task id and:

```bash
export DEST_ELASTIC_HOST="https://....found.io"
export DEST_ELASTIC_API_KEY="..."
python poll_reindex_task.py --task-id "<task-id-from-response>"
```

## Optional integration tests

`tests/test_integration_validate.py` runs `validate_migration.py` against **real** clusters when enabled.

| Variable | Required | Meaning |
|----------|----------|---------|
| `OSS_MIGRATION_INTEGRATION` | Yes | Set to `1` to un-skip the test |
| `SOURCE_OPENSEARCH_HOST` | Yes | Source URL |
| `DEST_ELASTIC_HOST` | Yes | Destination URL |
| `SOURCE_VALIDATION_INDEX` | Yes | Source index with known-good data |
| `DEST_VALIDATION_INDEX` | Yes | Destination mirror |
| `DEST_ELASTIC_API_KEY` or `DEST_ELASTIC_USER` + `DEST_ELASTIC_PASSWORD` | Yes | Dest auth |
| `SOURCE_OPENSEARCH_USER` + `SOURCE_OPENSEARCH_PASSWORD` | If not using SigV4 | Source basic auth |
| `OSS_MIGRATION_SAMPLE_SIZE` | No | Default `3` |
| `OSS_MIGRATION_SAMPLE_MODE` | No | Default `head` (`time_stratified` supported) |
| `OSS_MIGRATION_TIME_FIELD` | For `time_stratified` | e.g. `@timestamp` |
| `OSS_MIGRATION_TIME_BUCKETS` | No | Default `8` |
| `OSS_MIGRATION_TIMEOUT` | No | Seconds (default `120`) |

```bash
export OSS_MIGRATION_INTEGRATION=1
export SOURCE_OPENSEARCH_HOST=...
export DEST_ELASTIC_HOST=...
export SOURCE_VALIDATION_INDEX=my-smoke-src
export DEST_VALIDATION_INDEX=my-smoke-dest
export DEST_ELASTIC_API_KEY=...
python3 -m pytest tests/test_integration_validate.py -v
```

Default `pytest` still passes offline (integration test is **skipped** without `OSS_MIGRATION_INTEGRATION=1`).

Heavier HTTP mocks via `responses` are optional if you outgrow `unittest.mock`.

## GitHub Actions

- [.github/workflows/ci.yml](../.github/workflows/ci.yml) — `pytest`, non-blocking `pip-audit`, non-blocking `gitleaks`.
- [.github/workflows/ci-security-strict.yml](../.github/workflows/ci-security-strict.yml) — **blocking** audit + gitleaks; run on a schedule or manually for stricter checks.
