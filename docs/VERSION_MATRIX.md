# Version matrix

Compatibility is **customer-specific** (OpenSearch minor, Elasticsearch
minor, plugins, JVM, custom codecs). This page documents the version
expectations for **this repository's tooling** and the Lucene window
between OpenSearch and Elasticsearch sources — it is not a formal
Elastic support matrix.

> Run [`migrate compat-check`](COMPAT_CHECK.md) against your actual
> clusters before relying on this table. It probes both sides and
> reports the precise gap, plus any per-index quirks (k-NN, custom
> codec, ES 5/6 mapping artefacts).

## Lucene window: OpenSearch → Elasticsearch

OpenSearch and Elasticsearch share Lucene. Elasticsearch can read
segments written by Lucene `N` and `N-1`; anything older is rejected on
recovery. That means:

| OpenSearch major | Lucene major | Snapshot-restore compatible with | Document-streaming paths (B / D / F) |
|------------------|--------------|----------------------------------|--------------------------------------|
| OpenSearch 1.x   | Lucene 8     | ES 7.x, 8.x                      | All versions of ES 7+ |
| OpenSearch 2.x   | Lucene 9     | ES 8.x, 9.x                      | All versions of ES 7+ |
| OpenSearch 3.x   | Lucene 10    | ES 9.x                           | All versions of ES 7+ |

Two consequences worth being explicit about:

1. **Snapshot restore (and Path E, RFS, which uses Lucene under the
   hood) is bounded by the Lucene window.** OS 3.x → ES 8.x will fail
   at recovery because ES 8.x speaks Lucene 9, not 10. `compat-check`
   surfaces this as a cluster warning before you start.
2. **Document-streaming paths are immune to the Lucene window.** Paths
   B (Logstash), D (S3 staging) and F (capture/replay) ferry `_source`
   JSON via REST — the destination writes brand-new segments at its
   own Lucene version. Use these when the Lucene window doesn't fit.

## Per-index features that affect path selection

| Feature in source index | Path A | Path B | Path D | Path E | Notes |
|-------------------------|:------:|:------:|:------:|:------:|-------|
| Plain text + keyword + numerics | ✅ | ✅ | ✅ | ✅ | Default case. |
| `string` type (ES 5/6 legacy) | ⚠️ | ✅ via [hardened filter](../Logstash_input/README.md) | ✅ with sanitizer | ⚠️ | Use `migrate metadata` to flatten. |
| Multi-type mapping (ES 5/6) | ⚠️ | ✅ with sanitizer | ✅ with sanitizer | ⚠️ | `migrate metadata` resolves. |
| `index.knn=true` (OpenSearch k-NN) | ❌ | ⚠️ drop + re-embed | ⚠️ drop + re-embed | ❌ | RFS cannot reconstruct OS vector segments. See [SEMANTIC_MIGRATION.md](SEMANTIC_MIGRATION.md). |
| `index.codec=zstd*` / `qat_*` | ❌ | ✅ | ✅ | ❌ | RFS cannot read OS-only Lucene codecs. |
| `index.codec=best_compression` | ✅ | ✅ | ✅ | ✅ | Standard Lucene codec; both sides ship it. |
| Custom analysers / synonyms | ⚠️ | ⚠️ | ⚠️ | ⚠️ | Same plugin/dictionary must exist on the destination. `migrate metadata` copies the *template*, not the dictionary files. |

Legend: ✅ supported, ⚠️ supported with a transformation step,
❌ not viable.

## Python scripts

| Component | Versions |
|-----------|----------|
| Python | Tested in CI on **3.10, 3.11, 3.12** ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)). Python 3.9 dropped in May 2026 because `botocore` + `types-requests` no longer share a compatible `urllib3` on 3.9. |
| `boto3` / `botocore` | See [`requirements.txt`](../requirements.txt). |
| `requests` | See [`requirements.txt`](../requirements.txt). |
| `requests-aws4auth` | Required for SigV4 source auth. |

The CLIs are pinned at **toolkit version 1.3.x**
(`pyproject.toml`). The `migrate --version` flag reports the running
version. Each minor bump is documented in
[`CHANGELOG.md`](../CHANGELOG.md).

## Elasticsearch / Elastic Cloud

| Destination flavour | Supported paths | Notes |
|---------------------|-----------------|-------|
| Elasticsearch 7.x (self-managed) | B, D | Remote reindex from OpenSearch tested against ES 8/9; YMMV on 7.x. |
| Elasticsearch 8.x (self-managed) | A, B, D, E (within Lucene window) | Full feature parity. |
| Elasticsearch 9.x (self-managed) | A, B, D, E | Same as 8.x; Lucene 10 means OS 3.x → ES 9.x snapshot-restore works. |
| **Elastic Cloud Hosted** | A, B, D, E, F | Standard cloud deployment; remote reindex requires source reachable from Elastic egress IPs (or via the [Proxy](../Proxy/README.md)). |
| **Elastic Cloud Serverless** | B, D, E (with `--target-type ELASTICSEARCH_SERVERLESS`), F | **No remote reindex.** Native snapshot restore unsupported. Run `migrate metadata --target-type ELASTICSEARCH_SERVERLESS` first to strip forbidden settings. See [SERVERLESS.md](SERVERLESS.md). |

## OpenSearch (source)

| Source flavour | Supported paths | Notes |
|----------------|-----------------|-------|
| Amazon OpenSearch Service (public endpoint) | All | SigV4 from `boto3` credential chain. |
| Amazon OpenSearch Service (VPC endpoint) | B, D, E, F, A via Proxy | Migration compute must run inside the VPC (or peered) for B/D/E/F. See [NETWORK_TOPOLOGY.md](NETWORK_TOPOLOGY.md). |
| Amazon OpenSearch Serverless | B, D, F | No snapshot API → no Path A / E. |
| Self-hosted OpenSearch 1.x | All paths (subject to Lucene window) | Basic auth (`--source-user` / `--source-password`). |
| Self-hosted OpenSearch 2.x | All paths | Default for most enterprises today. |
| Self-hosted OpenSearch 3.x | B, D, F (and E only into ES 9.x) | Lucene 10 source; cannot snapshot-restore into ES 8.x. |

## Logstash

| Component | Notes |
|-----------|-------|
| Image tag | [`Logstash_input/Dockerfile`](../Logstash_input/Dockerfile) pins the Logstash major; rebuild after Logstash upgrades. |
| `logstash-input-opensearch` | Must match the Logstash major. The Dockerfile installs the plugin during build. |
| Hardened transform filter | Included in every pipeline (`logstash.conf`, `logstash_api_key.conf`, `logstash_s3.conf`). See [Logstash_input/README.md](../Logstash_input/README.md). |

## Reindex-from-Snapshot (upstream wrapped)

| Component | Notes |
|-----------|-------|
| Upstream image | [`opensearchproject/opensearch-migrations-rfs`](https://hub.docker.com/r/opensearchproject/opensearch-migrations-rfs) (pin a tag in production). The runner [`s3_migration/rfs_runner.py`](../s3_migration/rfs_runner.py) accepts `--upstream-image`. |
| Snapshot format | Must be readable by the bundled Lucene major. OS 3.x snapshots require an RFS build with Lucene 10 support. |
| Source-side coordination | RFS uses a `migrations_working_state` index on the **destination** for cross-worker locking; no state on the source. |

## Where this gets out of date

This document is updated alongside major changes to the toolkit. If you
hit a mismatch between this table and a current version of a tool, the
**`migrate <subcommand> --help`** output and the per-doc references
(linked above) are the source of truth. Open a ticket / PR and we'll
refresh this page.

**Action before production:** always run a small-index pilot under your
real network and auth configuration before relying on these expectations
end-to-end. `migrate compat-check` plus a one-index `s3-extract` →
`s3-load` round-trip is enough to surface most surprises in minutes.
