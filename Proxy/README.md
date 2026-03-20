# OpenSearch VPC proxy

HTTP reverse proxy that runs inside AWS with network access to an **Amazon OpenSearch Service VPC endpoint**. It accepts OpenSearch-style API requests, signs them with **SigV4**, and forwards them to the private endpoint. Use it when the domain has no public endpoint so that Logstash (or Elastic Cloud, via a public ALB) can reach OpenSearch.

## Configuration

All configuration is via environment variables (or a repo-root `.env` file if you run from this repository with `python-dotenv` installed—see [../bootstrap_env.py](../bootstrap_env.py)):

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENSEARCH_ENDPOINT` | Yes | Full base URL of the OpenSearch VPC endpoint (e.g. `https://vpc-your-domain-xxx.region.es.amazonaws.com`). No trailing slash. |
| `AWS_REGION` | No | AWS region for SigV4 (default: `us-east-1`). |
| `PROXY_LISTEN` | No | Listen address (default: `0.0.0.0:9200`). Use `localhost:9200` to accept only local connections. |
| `PROXY_USER` | No | If set (with `PROXY_PASSWORD`), incoming requests must include `Authorization: Basic ...`. Use when the proxy is exposed publicly (e.g. behind an ALB for Elastic Cloud). |
| `PROXY_PASSWORD` | No | Basic auth password for the proxy. |

**IAM:** The proxy uses the same credentials as the rest of the repo (instance role, task role, or env). The role must allow calling the OpenSearch API (e.g. `es:ESHttpGet`, `es:ESHttpPost`, `es:ESHttpPut`, `es:ESHttpHead` on the domain resource).

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

## Deployment summary

- **VPC / network:** Proxy must run where it can reach the OpenSearch VPC endpoint (same VPC or connected). Outbound 443 to OpenSearch; inbound from clients (Logstash on 9200, or ALB on 443 to targets).
- **IAM:** Execution role (EC2 instance role or ECS task role) needs permissions to call the OpenSearch API (e.g. `es:ESHttp*` on the domain).
- **No TLS on the proxy itself:** The proxy speaks HTTP. TLS is handled by the ALB (or another reverse proxy) when the proxy is exposed publicly.
