# `shadow_diff.py` — query-parity cutover gate

A small, deterministic gate to run **immediately before** flipping live
traffic from the source cluster to the destination. Replays a curated list
of queries against both clusters, compares results, and exits non-zero if
the destination drifts beyond your configured tolerance.

This is the in-repo equivalent of upstream's "shadow comparison" step —
without the Java capture-and-replay pipeline. You bring a list of queries
that represent your real workload (top dashboards, hottest API paths,
canary queries); `shadow_diff` does the rest.

## When to use it

- The data path (remote reindex / Logstash / S3 staging / RFS) reports
  success and `validate_migration.py` reports count + sample parity.
- Before you change DNS / Kibana data view / app config to point at the
  destination.
- As a periodic regression check while both clusters are dual-written.

## What it compares

Per query, four comparators (each independently tunable):

| Comparator | What it checks | Knob |
|------------|----------------|------|
| `status` | HTTP status parity (4xx/5xx is an instant fail). | implicit |
| `count` | `hits.total.value` parity. | `--count-tolerance` (default `0.0`) |
| `topk-ids` | Jaccard overlap of the top-K hit `_id` lists. | `--top-k`, `--topk-id-threshold` (default `1.0`) |
| `topk-hashes` | SHA-256 of canonicalised `_source` for IDs present in both. | `--topk-hash-threshold` (default `1.0`); disable with `--no-hashes` |

Pass thresholds tighter or looser to match your acceptance criteria. For
score-sensitive workloads where ranking drift is expected (e.g. minor
analyzer differences), set `--topk-id-threshold 0.8` to allow 20%
reordering; for "exact same docs in exact same order" use `1.0`.

## Query file format

A JSON list of objects:

```json
[
  {
    "name": "users-active-last-7d",
    "index": "users",
    "body": {"query": {"range": {"last_seen": {"gte": "now-7d"}}}, "size": 50}
  },
  {
    "name": "logs-error-rate",
    "index": "logs-2024-*",
    "body": {"query": {"term": {"level": "error"}}, "size": 100, "sort": [{"@timestamp": "desc"}]},
    "params": {"preference": "_primary"}
  }
]
```

Or a directory of `*.json` files (each can be a single object or a list).
Names are deduplicated across both sources; `--queries-file` wins on
collision.

## Quick start

```bash
python shadow_diff.py \
  --source-host "$SOURCE_OPENSEARCH_HOST" \
  --source-user "$SOURCE_OPENSEARCH_USER" \
  --source-password "$SOURCE_OPENSEARCH_PASSWORD" \
  --dest-host "$DEST_ELASTIC_HOST" \
  --dest-api-key "$DEST_ELASTIC_API_KEY" \
  --queries-file ./cutover-queries.json \
  --top-k 20 \
  --count-tolerance 0.0 \
  --topk-id-threshold 1.0 \
  --report ./shadow-diff-report.json \
  --strict-exit-codes --log-format json
```

SigV4 source: omit `--source-user/--source-password` and pass
`--source-region` (or set `AWS_REGION`); the AWS provider chain takes over.

`--workers N` runs queries in parallel (default 4). Use `--workers 1` for
deterministic output.

## Recommended sequencing

```
preflight        # source + dest basic health
metadata         # templates / pipelines (with sanitization)
<data path>      # one of: remote reindex, Logstash, S3 staging, RFS
validate         # count + sample parity per index
shadow-diff      # query parity at the workload level   ← cutover gate
flip traffic     # only if shadow-diff returns 0
```

`shadow-diff` is the **last** gate before traffic flip — it's where you
catch drift that count parity alone won't show (mapping conflicts,
analyzer differences, missing fields in `_source`, ranking anomalies).

## Building a query list

Three good ways:

1. **Top dashboards.** Export each Kibana visualisation's underlying ES
   query (Kibana → Inspect → Request) and drop them into
   `cutover-queries.json`.
2. **App canaries.** Whatever queries your application uses for SLOs.
3. **Slow-log replay.** Take the highest-cardinality entries from the
   source's slow log; if they pass shadow-diff, the rest probably will
   too. (For high-fidelity sampled real traffic, see [the capture/replay
   path](CAPTURE_REPLAY.md) — that proxy can write a query log
   `shadow_diff` can consume.)

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `transport` failure on every query | Network / auth issue against one side. | Run `preflight` to isolate; check API key / SigV4. |
| `topk-ids` drift but `count` passes | Score / ranking changed (analyzer differences, missing field). | Inspect failing query body; pre-create dest mapping with the same analyzer. |
| `topk-hashes` drift but `topk-ids` matches | Per-doc field drift (e.g. ingest pipeline rewrote a field on dest). | Check templates / pipelines on dest; rerun `metadata` migration. |
| Very low Jaccard with the same `count` and `topk-hashes` matches on overlap | Different scoring; same docs, different order. | Loosen `--topk-id-threshold` or sort by `_id` in the query. |

## See also

- [VALIDATION.md](VALIDATION.md) — index-level count + sample parity.
- [CAPTURE_REPLAY.md](CAPTURE_REPLAY.md) — sampled real-traffic replay.
- [AUTOMATION.md](AUTOMATION.md) — exit-code reference.
