# Capture & replay (Path F) — Python, document-level

Sampled cutover validation by recording real traffic on the source and
replaying it against the destination. Lightweight Python equivalent of
upstream OpenSearch Migrations' Java capture/replay pipeline.

## Scope, in two paragraphs

This is **NOT** a Kafka-backed petabyte-scale traffic mirror. It records
proxied requests + responses to NDJSON (locally or to S3) using the
existing SigV4 reverse proxy, then replays a filtered subset against the
destination and reports drift. Suitable for: validating that a destination
serves the same query workload during cutover; capturing a representative
query log for `shadow_diff`; recording a slow-log replay set; auditing
which clients hit which endpoints.

For full zero-loss high-fidelity capture and replay (multi-tenant
production traffic mirroring at scale), use upstream's Java pipeline. The
honest tradeoff for this in-repo path is **simplicity + Python-only ops
vs. fidelity at scale**.

## Components

| Tool | What it does |
|------|--------------|
| [`Proxy/app.py`](../Proxy/app.py) | Existing SigV4 reverse proxy. Now optionally tees every proxied request and response to NDJSON. |
| [`Proxy/capture.py`](../Proxy/capture.py) | Capture engine: bounded queue + background writer (local file rotation or gzipped S3 batch upload). Headers redacted, large bodies hashed-only. |
| [`replay/replayer.py`](../replay/replayer.py) | CLI that reads captured NDJSON (local or `s3://`) and replays it against the destination with status / size / hash comparators. |

## Capture: enabling it on the proxy

Capture is **off by default**. Set `PROXY_CAPTURE_MODE` to turn it on.

### Local file mode

```bash
export OPENSEARCH_ENDPOINT="https://vpc-xxx.us-east-1.es.amazonaws.com"
export PROXY_CAPTURE_MODE=local
export PROXY_CAPTURE_DIR=/var/log/proxy-capture
export PROXY_CAPTURE_PATH_INCLUDE='/_search,/_msearch'
export PROXY_CAPTURE_METHODS='GET,POST'
python -m Proxy.app
```

Files rotate every `PROXY_CAPTURE_ROTATE_BYTES` (default 100 MiB) or
`PROXY_CAPTURE_ROTATE_SECONDS` (default 60 s). Filenames look like
`capture-20260429T120145-12345-abc123.ndjson`.

### S3 mode

```bash
export PROXY_CAPTURE_MODE=s3
export PROXY_CAPTURE_S3_URI=s3://my-cap-bucket/proxy/
export AWS_REGION=us-east-1
python -m Proxy.app
```

Records buffer in memory, gzip-compress, and PUT to
`s3://bucket/prefix/capture-<ts>-<rand>.ndjson.gz` every
`PROXY_CAPTURE_ROTATE_SECONDS` or every ~1000 records. The proxy task
needs `s3:PutObject` on the prefix.

### Tuning

| Variable | Default | Notes |
|----------|---------|-------|
| `PROXY_CAPTURE_INCLUDE_BODIES` | `true` | If `false`, only sizes + SHA-256s are kept. Smaller capture, but limits replay (write requests can't be replayed without the body). |
| `PROXY_CAPTURE_MAX_BODY_BYTES` | `1048576` | Bodies larger than this are stored as size + hash only. Replayer flags them with `body-not-inlined`. |
| `PROXY_CAPTURE_PATH_INCLUDE` | `.*` | Comma-separated regex; only matching paths are captured. |
| `PROXY_CAPTURE_PATH_EXCLUDE` | _empty_ | Comma-separated regex; matches are dropped. |
| `PROXY_CAPTURE_METHODS` | `GET,POST,HEAD` | Whitelist of methods to capture. |
| `PROXY_CAPTURE_QUEUE` | `10000` | Bounded in-process queue. Drops on overflow (counter exposed via `Capturer.dropped`). |

### Capture record format

One JSON object per line, gzipped only on S3:

```json
{
  "ts": "2026-04-29T12:01:45.123456Z",
  "request_id": "8c2f...",
  "method": "POST",
  "path": "/idx/_search",
  "query_string": "preference=_primary",
  "request_headers": {"Content-Type": "application/json"},
  "request_body": "{\"query\":{\"match_all\":{}}}",
  "request_body_bytes": 27,
  "request_body_hash": "sha256:...",
  "response_status": 200,
  "response_headers": {"Content-Type": "application/json"},
  "response_body": "{\"hits\":...}",
  "response_body_bytes": 34123,
  "response_body_hash": "sha256:...",
  "latency_ms": 27,
  "target_host": "https://vpc-...es.amazonaws.com"
}
```

Headers known to leak credentials (`Authorization`, `Cookie`,
`X-Api-Key`, `X-Amz-Security-Token`, `Set-Cookie`, …) are stripped before
writing. Inspect `Proxy/capture.py::_REDACT_HEADERS` for the full list.

## Replay

```bash
python -m replay.replayer \
  --captures s3://my-cap-bucket/proxy/ \
  --dest-host "$DEST_ELASTIC_HOST" \
  --dest-api-key "$DEST_ELASTIC_API_KEY" \
  --method GET,POST \
  --path-include '/_search$' \
  --path-exclude '^/_cluster/' \
  --since 2026-04-29T11:00:00Z --until 2026-04-29T13:00:00Z \
  --max-requests 5000 \
  --rate-limit 50 \
  --size-tolerance 0.10 \
  --workers 4 \
  --report ./replay-report.json \
  --strict-exit-codes --log-format json
```

Local file or directory works the same way:

```bash
python -m replay.replayer --captures /var/log/proxy-capture/ ...
```

### Comparators

| Knob | What it checks | Toggle |
|------|----------------|--------|
| `status` | Captured vs replayed HTTP status. | `--no-status-check` |
| `size`   | Body byte count parity, within `--size-tolerance` (default 0.10 = 10%). Set negative to disable. | `--size-tolerance` |
| `hash`   | SHA-256 of the canonical (sorted-key) JSON body. Catches content drift while ignoring cosmetic key-order differences. | `--no-hash-check` |

### Filters

| Flag | Default | Notes |
|------|---------|-------|
| `--method` | `GET,POST` | Comma-separated allowed methods. |
| `--path-include` (repeatable) | none (=> all) | Regex match on captured path. |
| `--path-exclude` (repeatable) | none | Regex match on captured path. |
| `--since` / `--until` | none | ISO 8601 (UTC) bounds on captured `ts`. |
| `--max-requests` | none | Sample after filtering. |

### Exit codes (with `--strict-exit-codes`)

| Code | Meaning |
|------|---------|
| 0 | Every replayed request matched the comparator thresholds. |
| 2 | Configuration error (missing flags, no captures path, bad ISO timestamp). |
| 3 | Every replayed request errored (suggests a global auth / network issue). |
| 4 | One or more comparators drifted (others may have passed; the report has a sample). |

Pair with `shadow_diff` for a complete cutover gate:

```
shadow_diff (curated query parity) ──┐
                                     ├── both must return 0 before flipping traffic
replay (sampled real-traffic parity) ┘
```

## Operational sequencing

```
preflight                       # connectivity sanity
metadata                        # templates / pipelines / sanitization
<data path>                     # remote reindex / Logstash / S3 / RFS
validate                        # count + sample parity
shadow-diff                     # curated query parity
replay   (--max-requests 5000)  # sampled real-traffic parity
flip traffic                    # only after both gates exit 0
```

## What this path does NOT do

- **No Kafka durability.** Capture is a bounded in-process queue. If the
  proxy dies, queued records are lost. For a durable pipeline, use
  upstream's Kafka-backed capture.
- **No write-side replay safety.** The replayer happily replays `POST
  /idx/_doc/...` against the destination. If you don't want to mutate
  the destination, pass `--method GET` (or omit POST from the capture
  in the first place via `PROXY_CAPTURE_METHODS=GET`).
- **No request-time clock awareness.** The replay rate is governed by
  `--rate-limit`, not by replaying at the original spacing. Real-time
  reproduction would require a more complex scheduler.
- **No partial-body replay.** Records whose request body exceeded
  `PROXY_CAPTURE_MAX_BODY_BYTES` are stored as hash-only and the
  replayer flags them with `body-not-inlined`. Increase the limit if
  you need to replay them.

## See also

- [SHADOW_DIFF.md](SHADOW_DIFF.md) — curated-query cutover gate; pair
  with this path.
- [docs/AUTOMATION.md](AUTOMATION.md) — strict exit codes for both
  tools.
- [Proxy/README.md](../Proxy/README.md) — the underlying SigV4 proxy.
