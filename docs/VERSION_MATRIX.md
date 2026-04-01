# Version matrix (guidance)

Compatibility is **customer-specific** (OpenSearch minor, Elastic minor, plugins, JVM). This page sets **expectations** for this repository’s tooling—not a formal support matrix.

## Python scripts

| Component | Tested in CI |
|-----------|----------------|
| Python | **3.11** ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)); **3.9+** reasonable per [README](../README.md) |

## Elasticsearch / Elastic Cloud

| Feature | Notes |
|---------|--------|
| Remote `_reindex` with `source.remote` | **Elastic Cloud Hosted**; not Elastic Cloud **Serverless** ([SERVERLESS.md](SERVERLESS.md)) |
| Validate / preflight / poll | Works against standard Elasticsearch **REST** (8.x–9.x typical) |

## OpenSearch (source)

| Source | Notes |
|--------|--------|
| Amazon OpenSearch Service | SigV4 in `validate_migration` / `preflight` when **not** using basic auth |
| OpenSearch 1.x / 2.x | `_count`, `_search`, `_mget` as used here are widely available; confirm **fine-grained** roles allow these APIs |

## Logstash

| Component | Notes |
|-----------|--------|
| Image tag | See [Logstash_input/Dockerfile](../Logstash_input/Dockerfile) |
| `logstash-input-opensearch` | Must match Logstash major—rebuild image after upgrades |

**Action:** run a **non-production** dry run (preflight + small index validate) before production cutover on your exact versions.
