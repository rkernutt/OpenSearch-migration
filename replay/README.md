# `replay` — replay captured proxy traffic

CLI that reads NDJSON traffic captures produced by [`Proxy/capture.py`](../Proxy/capture.py)
and replays each request against an Elasticsearch / Elastic Cloud destination,
optionally comparing the response to the captured original.

This is the Python, document-level counterpart to upstream OpenSearch
Migrations' Java + Kafka capture/replay pipeline. Suitable for **sampled
cutover validation** (Path F) — not high-fidelity petabyte-scale traffic
mirroring. See [`docs/CAPTURE_REPLAY.md`](../docs/CAPTURE_REPLAY.md) for the
full pipeline rationale and trade-offs.

## What it does

1. Loads NDJSON capture files from one or more sources:
   - Local file (`./capture-20260429T120145.ndjson`).
   - Local directory (recursively reads `*.ndjson` and `*.ndjson.gz`).
   - S3 prefix (`s3://bucket/prefix/`) — reads every `*.ndjson(.gz)` under it.
2. Filters records by method, path regex (include/exclude), time window
   (`--since` / `--until`), and absolute count (`--max-requests`).
3. Replays each surviving request against the destination using the same
   path, query string, headers, and body as the captured original. Auth
   headers come from CLI flags / env (`DEST_ELASTIC_HOST` + API key or
   user/pass) — the captured `Authorization` was redacted at capture time.
4. Compares the replayed response to the captured original using three
   comparators:
   - **Status** — exact match (`--no-status-check` to skip).
   - **Size** — within `--size-tolerance` (fractional, default `0.10`).
   - **Hash** — canonical-JSON SHA-256 of the response body, falling back to
     a literal-bytes hash for non-JSON. `--no-hash-check` to skip.
5. Emits per-request results, an aggregated JSON summary line, and (with
   `--report PATH`) a full JSON report.

## Usage

Minimum:

```bash
migrate replay \
  --captures ./proxy-captures/ \
  --dest-host "$DEST_ELASTIC_HOST" \
  --dest-api-key "$DEST_ELASTIC_API_KEY"
```

Realistic Serverless cutover gate:

```bash
migrate replay \
  --captures s3://my-cap-bucket/proxy/2026-04-29/ \
  --dest-host "$DEST_ELASTIC_HOST" --dest-api-key "$DEST_ELASTIC_API_KEY" \
  --method GET \
  --path-include '/_search' --path-include '/_msearch' \
  --since 2026-04-29T12:00:00Z --until 2026-04-29T13:00:00Z \
  --max-requests 5000 \
  --workers 8 --rate-limit 50 \
  --size-tolerance 0.05 \
  --report ./replay-report.json \
  --strict-exit-codes --log-format json
```

Notes:

- **Default `--method` is `GET,POST`.** To avoid mutating the destination,
  restrict to `GET` (or limit which methods were captured by setting
  `PROXY_CAPTURE_METHODS=GET` upstream).
- **`--rate-limit`** is requests per second across all workers (token bucket).
  `0` disables rate limiting.
- **Bodies that exceeded `PROXY_CAPTURE_MAX_BODY_BYTES`** at capture time were
  stored as size + SHA-256 only; the replayer flags those records with
  `body-not-inlined` and skips them rather than sending an empty body.

## Exit codes (`--strict-exit-codes`)

| Code | Meaning |
|------|---------|
| `0` | All replayed responses matched within the configured tolerances. |
| `2` | Configuration error (missing flags, no captures found, unreadable region, bad regex, etc.). |
| `3` | Transport / auth / TLS failure on destination (after retries). |
| `4` | Drift detected: at least one comparator failed (status / size / hash) on at least one record. |

`--report ./replay-report.json` writes a per-record breakdown of every
mismatch so CI can attach it to the build artifacts.

## Pair with

- [`Proxy/capture.py`](../Proxy/capture.py) — the producer side. Enable with
  `PROXY_CAPTURE_MODE=local|s3` on the SigV4 reverse proxy.
- [`shadow_diff.py`](../shadow_diff.py) — the curated-query equivalent. The
  replayer covers *real* traffic; `shadow_diff` covers *known-important*
  queries that you can write down once.

## Documentation

- [`docs/CAPTURE_REPLAY.md`](../docs/CAPTURE_REPLAY.md) — end-to-end runbook,
  capture record format, scope/trade-offs, and explicit comparison to upstream's
  Java + Kafka pipeline.
- [`docs/SHADOW_DIFF.md`](../docs/SHADOW_DIFF.md) — companion cutover gate
  (curated queries).
- [`docs/AUTOMATION.md`](../docs/AUTOMATION.md) — exit codes and `make` targets.
- [`docs/TOOLS.md`](../docs/TOOLS.md) — single-page index of every CLI in the repo.
