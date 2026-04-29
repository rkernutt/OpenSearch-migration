# S3-staged migration (Path D)

End-to-end runbook for the **`s3_migration`** package: extract OpenSearch
indices to gzipped NDJSON parts in S3, then bulk-load them into Elastic Cloud
(Hosted **or** Serverless). Pairs with `validate_migration.py` for post-load
reconciliation.

> Looking for the snapshot-based path that uses upstream's Lucene-aware RFS?
> See [RFS.md](RFS.md) (Path E).

## When to use this path

- The OpenSearch domain is **VPC-only** or **IAM-only** and Elastic Cloud
  cannot reach it directly. Either run `s3_migration.s3_extract` from a host
  inside the VPC, or point it at the in-repo SigV4 reverse proxy.
- The destination is **Elastic Serverless** (remote reindex is not supported).
- You want to **decouple** extract and load — pause, replay, hand the bucket
  off to a different operator, or load the same data into multiple targets.
- You already have NDJSON in S3 (partner data, `elasticdump` exports,
  archived dumps) and need to load it.

## When *not* to use this path

- Elastic Hosted ↔ OpenSearch are network-reachable and you can use **remote
  reindex** ([Remote_Reindex/README.md](../Remote_Reindex/README.md)) — that's
  the simplest option.
- You need true **zero-downtime cutover** with shadow comparison — that's a
  Logstash + dual-write or upstream Capture-and-Replay job.
- Your source is huge (multi-TB) and you already snapshot to S3 — Path E
  (wrapped RFS) is faster because it skips re-serialising every document.

## Components

| Tool | What it does |
|------|--------------|
| [`s3_migration.s3_extract`](../s3_migration/s3_extract.py) | Sliced-scroll OpenSearch into gzipped NDJSON parts in S3 with a manifest. |
| [`s3_migration.s3_bulk_load`](../s3_migration/s3_bulk_load.py) | Stream NDJSON.gz parts from S3 into Elastic via `_bulk` with retries, DLQ, checkpointing. |
| [`s3_migration.s3_common`](../s3_migration/s3_common.py) | Shared helpers (`S3Uri`, `Manifest`, batching, gzipped streaming). |
| [`Logstash_input/pipeline/logstash_s3.conf`](../Logstash_input/pipeline/logstash_s3.conf) | Alternative loader for source-only NDJSON when you want Logstash filters. |
| [`validate_migration.py`](../validate_migration.py) | Post-load count + sampling reconciliation (works against Serverless). |

## On-disk format

```
s3://bucket/<job-prefix>/
  _manifest.json
  data/<index>/slice-NNN-part-MMMMM.ndjson.gz
  dlq/<job-prefix>/<part-name>__failed.ndjson.gz   (only if the loader writes failures)
```

Each part is **bulk-format NDJSON**: alternating action and source lines, the
exact bytes consumed by Elasticsearch's `_bulk` API. The loader also accepts
**source-only NDJSON** (one source document per line) when you pass
`--target-index`.

The manifest records source host, auth label, per-index `_count`, and per-part
size / doc count, so the loader (and `validate_migration.py`) can reconcile.

## Auth modes

Mirrors the rest of the toolkit:

| Mode | How to invoke | Uses |
|------|---------------|------|
| **SigV4** (Amazon OpenSearch Service) | Default when `--source-user`/`--source-password` are not set; pass `--source-region`. | The AWS provider chain (env vars / instance role / `~/.aws/credentials`). |
| **Basic auth** | Pass `--source-user` / `--source-password` (or set `SOURCE_OPENSEARCH_USER` / `SOURCE_OPENSEARCH_PASSWORD`). | Fine-grained-access OpenSearch. |
| **Via SigV4 reverse proxy** | Point `--source-host` at the proxy URL with the proxy's basic-auth credentials, then add `--via-proxy` for an audit-friendly manifest tag. | The in-repo [`Proxy/`](../Proxy/) re-signs to OpenSearch. |

## Quick start

```bash
# 1. Extract — writes parts and a manifest under the s3://.../ prefix.
python -m s3_migration.s3_extract \
  --source-host "$SOURCE_OPENSEARCH_HOST" \
  --source-user "$SOURCE_OPENSEARCH_USER" \
  --source-password "$SOURCE_OPENSEARCH_PASSWORD" \
  --indices "logs-2024,metrics-2024" \
  --s3-uri "s3://my-bucket/migration/2026-04-29/" \
  --slices 4 \
  --part-size-mb 64 \
  --strict-exit-codes --log-format json \
  --checkpoint-file ./.extract.ckpt

# 2. Load — pointed at the same prefix.
python -m s3_migration.s3_bulk_load \
  --s3-uri "s3://my-bucket/migration/2026-04-29/" \
  --dest-host "$DEST_ELASTIC_HOST" \
  --dest-api-key "$DEST_ELASTIC_API_KEY" \
  --batch-size-mb 5 --max-in-flight 4 \
  --strict-exit-codes --log-format json \
  --checkpoint-file ./.bulk-load.ckpt

# 3. Validate — counts and sampled IDs on the destination.
python validate_migration.py \
  --indices "logs-2024,metrics-2024" \
  --check-existence --sample-size 50 \
  --strict-exit-codes --output-format json
```

Exit codes are documented in [AUTOMATION.md](AUTOMATION.md). The summary line
on stdout (with `--log-format json`) is a single object designed for Tines /
Step Functions / Jenkins consumption.

## Resuming, replaying, and DLQ

- **Resume the extractor.** Use `--checkpoint-file`. Slice IDs (0..N-1) must
  stay stable across resumes; if you change `--slices`, delete the checkpoint
  and the partial S3 prefix.
- **Resume the loader.** Use `--checkpoint-file`. Each completed part key is
  recorded; subsequent runs skip them.
- **Replay a load** (e.g. into a second region). Re-run `s3_bulk_load` with a
  fresh `--checkpoint-file` against the same S3 prefix, pointed at the new
  destination. Idempotent because each part carries its own `_id` values.
- **DLQ.** Per-document failures are written to
  `s3://.../dlq/<job-prefix>/<part>__failed.ndjson.gz`. To retry only those
  failures, point a second `s3_bulk_load` invocation at the DLQ prefix.

## Tuning checklist

| Knob | Default | When to change |
|------|---------|----------------|
| `--slices` (extract) | 4 | Up to ~`shards × 2` for large indices; keep ≤ source CPU. |
| `--page-size` (extract) | 1000 | Larger pages = fewer round-trips, more memory and bigger payloads. |
| `--part-size-mb` (extract) | 64 | Smaller = more parts but more parallel-load potential and faster resume. |
| `--batch-size-mb` (load) | 5 | Up to ~15 (Elastic Cloud's `http.max_content_length`); the sweet spot is usually 5–10 MB. |
| `--max-in-flight` (load) | 4 | Cap on concurrent `_bulk` posts. Watch destination ingest pressure. |
| Destination `refresh_interval` | `1s` | Set to `-1` during a heavy load and restore (with `_refresh`) before validation. |
| Destination `number_of_replicas` | env-default | `0` during a heavy load on Elastic Hosted; restore after. |

> Elastic **Serverless** ignores shard / replica / refresh tuning — those are
> platform-managed. Just monitor ingest latency and back off
> `--max-in-flight` if the destination starts rejecting.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Loader exits **3** with "failed to enumerate S3 parts" | S3 listing failed (auth, region, network). | Set `AWS_REGION`, check IAM (`s3:ListBucket`, `s3:GetObject`), confirm bucket region. |
| Loader exits **4** with `dlq_used: true` in summary | Per-document failures (mapping, version conflict, etc.). | Inspect the DLQ object; pre-create destination index with the right mapping or sanitize source data and retry the DLQ prefix. |
| Loader hits 429s repeatedly | Destination ingest saturated. | Lower `--max-in-flight`, raise `--batch-size-mb` slightly, or pre-set `refresh_interval: -1`. The shared session already retries 429 with backoff. |
| Extractor: `slice X failed: ConnectionError` | Network blip during a long scroll. | Re-run with the same `--checkpoint-file`. Completed slices skip; failed slice restarts from 0 (idempotent because we deduplicate by `_id`). |
| Extractor: `_count` warning, extract continues | Source `_count` requires a privilege missing from the migration role. | Non-fatal; the manifest's `doc_count_source` will be null. Validate with `--check-existence` after load instead. |
| Manifest unreadable, exit **2** | Stale `_manifest.json` from a previous incompatible job. | Delete the manifest object and re-run; the loader will fall back to listing parts. |
| Loader can't talk to S3 endpoint inside VPC | No VPC endpoint / NAT gateway. | Add an S3 gateway endpoint or run the loader from outside the VPC against a public bucket. |

## Security notes

- `_manifest.json` records the **source host** and **auth label** but never
  credentials. The loader and extractor both reuse the redaction helper from
  `validate_migration.py` so error bodies don't leak API keys.
- Use a **scoped IAM role** for the extractor (`s3:PutObject` on the job
  prefix, `s3:AbortMultipartUpload` for resumes) and a **read-only role** for
  the loader (`s3:GetObject`, `s3:ListBucket` on the same prefix, plus
  `s3:PutObject` on the DLQ prefix).
- Treat `--dest-api-key` exactly like the rest of the toolkit: prefer env
  vars, never check it into shell history. The summary log line never
  contains it.
- For Serverless: use a **scoped API key** (per-index privileges) rather than
  cluster-admin, even during one-off migrations.

## See also

- [AUTOMATION.md](AUTOMATION.md) — exit codes for `s3_extract` / `s3_bulk_load`.
- [RFS.md](RFS.md) — when you'd use the snapshot-based path instead.
- [SERVERLESS.md](SERVERLESS.md) — Elastic Serverless destination notes.
- [RUNBOOK.md](../RUNBOOK.md) — full migration runbook including this path.
