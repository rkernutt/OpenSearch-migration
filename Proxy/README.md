# OpenSearch VPC proxy (with optional capture)

HTTP reverse proxy that runs inside AWS with network access to an **Amazon OpenSearch Service VPC endpoint**. It accepts OpenSearch-style API requests, signs them with **SigV4**, and forwards them to the private endpoint. Use it when the domain has no public endpoint so that Logstash (or Elastic Cloud, via a public ALB) can reach OpenSearch.

It can also **tee every proxied request and response to NDJSON** (locally or to S3) for later replay against the destination — see [Capture mode](#capture-mode-optional) below and [docs/CAPTURE_REPLAY.md](../docs/CAPTURE_REPLAY.md).

## Configuration

All configuration is via environment variables (or a repo-root `.env` file if you run from this repository with `python-dotenv` installed—see [../bootstrap_env.py](../bootstrap_env.py)):

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENSEARCH_ENDPOINT` | Yes | Full base URL of the OpenSearch VPC endpoint (e.g. `https://vpc-your-domain-xxx.region.es.amazonaws.com`). No trailing slash. |
| `AWS_REGION` | No | AWS region for SigV4 (default: `us-east-1`). |
| `PROXY_LISTEN` | No | Listen address (default: `0.0.0.0:9200`). Use `localhost:9200` to accept only local connections. |
| `PROXY_USER` | No | If set (with `PROXY_PASSWORD`), incoming requests must include `Authorization: Basic ...`. Use when the proxy is exposed publicly (e.g. behind an ALB for Elastic Cloud). |
| `PROXY_PASSWORD` | No | Basic auth password for the proxy. |
| `PROXY_MAX_BODY_MB` | No | Max request body size in MB (default: `100`). Larger bulk payloads may require raising this; very high values increase memory risk. |
| `PROXY_VERIFY_TLS` | No | Set to `false` to disable TLS verification of the OpenSearch endpoint (not recommended in production). Set `PROXY_CA_BUNDLE` instead for custom CA certificates. |
| `PROXY_CA_BUNDLE` | No | Path to a CA bundle file (PEM) for verifying the OpenSearch endpoint TLS certificate (e.g. self-signed or private CA). |
| `PROXY_DEBUG` | No | Set to `1` to log `METHOD /path → status (Xms)` on stderr. Bodies are never logged. |
| `PROXY_CAPTURE_MODE` | No | `off` (default), `local`, or `s3`. Enables the request/response capture engine; see [Capture mode](#capture-mode-optional). |
| `PROXY_CAPTURE_DIR` | If `mode=local` | Directory where rotating NDJSON files are written. Created if missing. |
| `PROXY_CAPTURE_S3_URI` | If `mode=s3` | `s3://bucket/prefix` for gzipped NDJSON uploads. Region uses `AWS_REGION`. |
| `PROXY_CAPTURE_INCLUDE_BODIES` | No | `true` (default) keeps inline bodies under `PROXY_CAPTURE_MAX_BODY_BYTES`; `false` keeps only sizes + SHA-256s. |
| `PROXY_CAPTURE_MAX_BODY_BYTES` | No | Inline cap per body (default `1048576` = 1 MiB). |
| `PROXY_CAPTURE_PATH_INCLUDE` | No | Comma-separated regex; only matching paths are captured (default `.*`). |
| `PROXY_CAPTURE_PATH_EXCLUDE` | No | Comma-separated regex; matches are dropped (e.g. `_cluster/.*,_nodes/.*`). |
| `PROXY_CAPTURE_METHODS` | No | Methods to capture (default `GET,POST,HEAD`). |
| `PROXY_CAPTURE_ROTATE_BYTES` | No | Local-mode rotate threshold (default 100 MiB). |
| `PROXY_CAPTURE_ROTATE_SECONDS` | No | Local- and S3-mode rotate timer (default 60 s). |
| `PROXY_CAPTURE_QUEUE` | No | Bounded in-process queue size (default 10000). Records past this drop with a counter. |

**IAM:** The proxy uses the same credentials as the rest of the repo (instance role, task role, or env). The role must allow calling the OpenSearch API (e.g. `es:ESHttpGet`, `es:ESHttpPost`, `es:ESHttpPut`, `es:ESHttpHead` on the domain resource). For a **read-only** validation role, only `es:ESHttpGet`, `es:ESHttpHead`, and `es:ESHttpPost` are required.

### Forwarded headers

The proxy forwards a **fixed whitelist** of headers to OpenSearch and back to the client. This prevents credential leakage via forwarded `Authorization` headers (the proxy provides its own SigV4 auth) while preserving content negotiation.

**Client → OpenSearch (request):** `content-type`, `accept`, `accept-encoding`

**OpenSearch → client (response):** `content-type`, `content-length`, `accept-ranges`

The proxy intentionally **drops** incoming `Authorization` and `Cookie` headers from clients — the SigV4 signature it generates replaces them. If a client sends `Authorization: Basic ...` to use proxy basic auth, that header is consumed by Flask before it reaches the forwarding layer.

**Health check:** `GET /health` returns `{"status": "ok"}` without requiring SigV4 or proxy auth. Use this for ALB target-group health checks.

## Run locally

```bash
cd Proxy
pip install -r requirements.txt
export OPENSEARCH_ENDPOINT="https://vpc-your-domain-xxx.region.es.amazonaws.com"
export AWS_REGION="us-east-1"
python app.py
```

Then from the same host (or another host with network access to this one):

```bash
curl -X GET "http://localhost:9200/_cluster/health?pretty"
```

## Run with Docker

```bash
cd Proxy
docker build -t opensearch-proxy .
docker run --rm -e OPENSEARCH_ENDPOINT="https://vpc-xxx.region.es.amazonaws.com" -e AWS_REGION="us-east-1" -p 9200:9200 opensearch-proxy
```

Override `PROXY_LISTEN` if needed (and match the port in `-p`).

## Use case 1: Logstash in the same VPC

1. Run the proxy in the same VPC (or a peered VPC) that can reach the OpenSearch VPC endpoint. For example, run the proxy on the same EC2 or ECS task as Logstash, or on a separate host that Logstash can reach.
2. Start the proxy with `OPENSEARCH_ENDPOINT` and `AWS_REGION`. You do not need `PROXY_USER`/`PROXY_PASSWORD` if only Logstash (on a private network) talks to the proxy.
3. Point Logstash at the proxy: in [sample_logstash_proxy.conf](../Logstash_input/sample_logstash_proxy.conf), set `hosts => 'http://localhost:9200'` (if Logstash and proxy are on the same host) or `http://<proxy-host>:9200`. If you enabled proxy basic auth, set `user` and `password` in the Logstash opensearch input to match `PROXY_USER` and `PROXY_PASSWORD`.
4. Run the Logstash pipeline; it will read from OpenSearch via the proxy.

**Security group:** Allow inbound TCP 9200 only from the Logstash host(s). Outbound 443 to the OpenSearch VPC endpoint.

## Use case 2: Elastic Cloud remote reindex (proxy public)

For [remote reindex](https://www.elastic.co/guide/en/elasticsearch/reference/current/docs-reindex.html) from Elastic Cloud, Elastic’s cluster must be able to call the OpenSearch domain. When the domain is VPC-only, put the proxy in front and expose it with TLS and auth:

1. **Run the proxy** in a subnet that can reach the OpenSearch VPC endpoint (same VPC or connectivity via peering/Transit Gateway). Set `OPENSEARCH_ENDPOINT`, `AWS_REGION`, and **set `PROXY_USER` and `PROXY_PASSWORD`** so only authenticated callers can use the proxy.
2. **Put an Application Load Balancer (ALB) in front** of the proxy:
   - ALB listener: HTTPS (443) with a certificate (e.g. from ACM).
   - Target group: proxy instances/containers on port 9200 (HTTP is fine; TLS terminates at the ALB).
3. **Security groups:** ALB allows inbound 443 from the internet (or restrict to [Elastic Cloud egress IPs](https://www.elastic.co/guide/en/cloud/current/ec-ip-addresses.html) if published). Proxy target group allows inbound 9200 only from the ALB.
4. **Elastic Cloud:** In deployment user settings, set `reindex.remote.whitelist: ["<alb-dns-name>:443"]`. In Dev Tools, run a reindex request with `source.remote.host` = `https://<alb-dns-name>:443` and `source.remote.username` / `source.remote.password` = your `PROXY_USER` / `PROXY_PASSWORD`.

This way Elastic Cloud talks HTTPS to the ALB, and the ALB forwards HTTP to the proxy; the proxy forwards HTTPS (SigV4) to OpenSearch.

## Capture mode (optional)

Set `PROXY_CAPTURE_MODE=local` (or `s3`) to record every proxied request and response as NDJSON. Pair with [`replay/replayer.py`](../replay/replayer.py) for sampled cutover validation against the destination — see [docs/CAPTURE_REPLAY.md](../docs/CAPTURE_REPLAY.md) for the full pipeline.

### What gets captured

Each proxied non-streamed request becomes a single JSON line with:

- Timestamp, request ID, target host, latency.
- Method, path, query string.
- Request and response headers, **redacted**: `Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key`, `X-Amz-Security-Token`, `Proxy-Authorization`, etc. are stripped before writing. The full redaction list is in [`Proxy/capture.py::_REDACT_HEADERS`](capture.py).
- Inline request and response **bodies up to `PROXY_CAPTURE_MAX_BODY_BYTES`** (default 1 MiB). Larger bodies are stored as size + SHA-256 only; the replayer flags requests whose body wasn't inlined with `body-not-inlined` rather than silently sending an empty body.

Streamed responses (currently only `GET` / `HEAD`, where the proxy iterates response chunks) are **not** captured — that would defeat the streaming optimisation. Run captures with `PROXY_CAPTURE_METHODS=GET,POST,HEAD` to record everything else.

### Local-file mode

```bash
export OPENSEARCH_ENDPOINT="https://vpc-xxx.us-east-1.es.amazonaws.com"
export PROXY_CAPTURE_MODE=local
export PROXY_CAPTURE_DIR=/var/log/proxy-capture
export PROXY_CAPTURE_PATH_INCLUDE='/_search,/_msearch'
export PROXY_CAPTURE_METHODS='GET,POST'
python -m Proxy.app
```

Files rotate by size (`PROXY_CAPTURE_ROTATE_BYTES`, default 100 MiB) **or** by time (`PROXY_CAPTURE_ROTATE_SECONDS`, default 60 s) — whichever comes first. Filenames look like `capture-20260429T120145-12345-abc123.ndjson`.

### S3 mode

```bash
export PROXY_CAPTURE_MODE=s3
export PROXY_CAPTURE_S3_URI=s3://my-cap-bucket/proxy/
export AWS_REGION=us-east-1
python -m Proxy.app
```

A background worker batches records, gzips them, and PUTs to `s3://bucket/prefix/capture-<ts>-<rand>.ndjson.gz`. The proxy's IAM role needs `s3:PutObject` on the prefix.

### Operational guarantees

- **Capture never blocks the proxy.** A bounded in-process queue is fed from the request handler; a background worker drains it. If the queue is full (e.g. the worker is throttled by S3), records are dropped and counted (`Capturer.dropped`); the proxy still serves the live request normally.
- **Capture failures never break the proxy.** Exceptions in the writer are logged (and counted in `Capturer.errors`) and the proxy continues.
- **Out-of-process replay.** The replayer is a separate CLI ([`python -m replay.replayer`](../replay/replayer.py)) — capture and replay can happen on different hosts, accounts, or clusters.

### Caveats

- **Not a Kafka pipeline.** Records that are queued but not yet written are lost if the proxy crashes. For zero-loss high-fidelity capture, use upstream OpenSearch Migrations' Java + Kafka stack.
- **Inline body cap matters.** Bulk-write traffic with very large bodies (multi-MiB `_bulk` requests) won't be replayable unless you raise `PROXY_CAPTURE_MAX_BODY_BYTES`. The trade-off is capture volume.
- **Replay is opt-in.** The replayer's default `--method` is `GET,POST`. To prevent accidentally re-mutating the destination during a replay, restrict to `GET` (or omit `POST`/`PUT`/`DELETE` from the capture in the first place via `PROXY_CAPTURE_METHODS`).

## Production deployment (Gunicorn + systemd)

Flask's built-in server (`python app.py`) is suitable for local testing only. For production, use **Gunicorn**:

```bash
# Install dependencies (in a virtualenv)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Run directly
.venv/bin/gunicorn --workers 4 --bind 0.0.0.0:9200 --timeout 120 app:app
```

A **systemd unit file** is provided at [`opensearch-proxy.service`](opensearch-proxy.service) for host-based deployments:

```bash
sudo cp opensearch-proxy.service /etc/systemd/system/
# Edit the unit to set OPENSEARCH_ENDPOINT and other env vars
sudo systemctl daemon-reload
sudo systemctl enable --now opensearch-proxy
journalctl -u opensearch-proxy -f
```

Worker count guidance: use `2–4 × CPU cores` for this I/O-bound proxy. The default in the unit file is 4.

For **container** deployments, the provided `Dockerfile` already uses Gunicorn.

## Deployment summary

- **VPC / network:** Proxy must run where it can reach the OpenSearch VPC endpoint (same VPC or connected). Outbound 443 to OpenSearch; inbound from clients (Logstash on 9200, or ALB on 443 to targets).
- **IAM:** Execution role (EC2 instance role or ECS task role) needs permissions to call the OpenSearch API (e.g. `es:ESHttp*` on the domain).
- **No TLS on the proxy itself:** The proxy speaks HTTP. TLS is handled by the ALB (or another reverse proxy) when the proxy is exposed publicly.
