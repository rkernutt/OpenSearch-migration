# Troubleshooting

Common errors and fixes, grouped by symptom. For per-script exit codes
see [AUTOMATION.md](AUTOMATION.md). For pre-flight gates that catch
many of these before they happen, see [COMPAT_CHECK.md](COMPAT_CHECK.md).

If your symptom is "tool exits non-zero but logs look fine", check the
exit-code table in [AUTOMATION.md](AUTOMATION.md) first — `2` is config,
`3` is transport, `4` is domain-specific (parity drift, doc errors,
compat warnings).

## Connectivity, TLS, auth

### `requests.exceptions.ConnectionError` / `Connection refused`

The migration host can't reach the endpoint at IP level.

- **VPC source:** ensure the migration compute lives **inside** the
  source VPC (or has a routed path via VPC peering / Transit Gateway).
  See [NETWORK_TOPOLOGY.md](NETWORK_TOPOLOGY.md).
- **PrivateLink destination:** confirm the Elastic Cloud VPC endpoint is
  *accepted* and *private DNS* is enabled on the endpoint. From the
  compute host: `dig +short your-deployment.es.region.aws.found.io`
  should return the **private** IP of the endpoint, not a public one.
- **Proxy in front:** check the proxy is reachable and the Elastic
  Cloud allow-list contains the proxy's public IP (or ALB DNS name) for
  remote reindex.

### `SSL: CERTIFICATE_VERIFY_FAILED`

- Self-signed OpenSearch certs need `--insecure` (CLI) or
  `requests`-level CA bundle config. The CLIs deliberately do **not**
  ship an `--insecure` flag because production migrations should not
  disable verification; either get a real cert on the cluster or set
  `REQUESTS_CA_BUNDLE` to a CA file.
- ALB / NLB in front of the Proxy must present a valid certificate for
  the hostname Elastic Cloud connects to. ACM-issued certs work; mixed
  HTTP/HTTPS does not.

### `401 Unauthorized` from Elastic

- API key is invalid or expired. Rotate via Stack Management → API
  keys; update `DEST_ELASTIC_API_KEY` in `.env` or your secret manager.
- API key is base64-encoded twice. The toolkit auto-encodes a raw
  `id:secret`; pass `--dest-api-key-encoded` only if you've pre-encoded
  it yourself.
- API key has insufficient privileges. Run with a temporary high-priv
  key to isolate; minimum privileges for each path are listed in
  [SECURITY.md](../SECURITY.md).

### `403 Forbidden` from OpenSearch

- Fine-grained access control (FGAC) on Amazon OpenSearch denies the
  request. The migration role/user needs `indices:data/read/*` on the
  source indices and `cluster:monitor/*` for `_cluster/health` /
  `_cat/*`.
- For SigV4, confirm the AWS principal hitting the cluster is mapped in
  OpenSearch Security → Role mappings → `all_access` (or a role with
  the right index privileges).

### `403` only on `_settings` / `_mapping` (compat-check or metadata)

`compat_check.py` and `metadata_migration.migrator` both call
`GET /<index>/_settings` and `GET /<index>/_mapping`. FGAC roles that
allow `_search` but not `_mapping` look fine for queries and fail here.
Grant `indices:admin/get`, `indices:admin/mappings/get`,
`indices:admin/settings/get` to the migration role.

### `RegionMismatchException` / SigV4 signing failure

`AWS_REGION` (or `--source-region`) must match the region the
OpenSearch domain is in. Domains in `eu-west-1` do not validate
signatures from `us-east-1`.

## Compatibility / mapping rejections

### `mapper_parsing_exception` on bulk load

Destination index mapping rejects a document. The most common causes:

- **ES 5/6 `string` type** in the source. Run
  [`migrate sanitize`](METADATA_MIGRATION.md) on the source mapping
  before pre-creating the destination index, or run
  `migrate metadata` to migrate templates with sanitization applied.
- **Multi-type mapping** (top-level type names other than `_doc`).
  Same fix — `migrate metadata` flattens to typeless mappings.
- **Strict dynamic mapping** on the destination plus a field the source
  has but the template doesn't. Either add the field to the template
  or relax `dynamic` on the destination.

`compat_check` flags these as `warn` before you hit them in production.

### `illegal_argument_exception` "unsupported codec"

The source index uses an OpenSearch-only codec
(`zstd`, `zstd_no_dict`, `qat_*`) and you're trying RFS (Path E). RFS
cannot reconstruct segments with codecs Elasticsearch doesn't ship.
Switch to Path D (S3 staging) or Path B (Logstash) — they read source
documents via `_search`/`_scroll`, never via Lucene segments, so they
sidestep this entirely.

### `mapper_parsing_exception: failed to parse field [vec]` — k-NN

OpenSearch k-NN vectors and Elasticsearch `dense_vector` have different
on-disk formats. The source `knn_vector` field is rejected on the
destination because the destination expects `dense_vector` (or
`semantic_text`).

Fix: re-embed on the destination. Drop the source vector field on the
way through (the [hardened Logstash filter](../Logstash_input/README.md)
has a commented-out `remove_field => [ "knn_vector" ]` example) and run
an inference pipeline on the destination. See
[SEMANTIC_MIGRATION.md](SEMANTIC_MIGRATION.md).

### `version_conflict_engine_exception` during bulk replay

You set `version_type` to `external` or `external_gte` and the source's
`_version` doesn't monotonically increase. Bulk replays / retries then
conflict.

Fix: do **not** propagate source `_version` to the destination unless
you intentionally need optimistic concurrency. The
[hardened Logstash filter](../Logstash_input/README.md) and the toolkit's
S3 loader both omit version by default. If you're using a custom
script, remove `_version` from the bulk action lines.

### `forbidden_setting` on Elastic Cloud Serverless

Settings like `index.number_of_shards`, `index.refresh_interval`,
`index.translog.*` are not user-tunable on Serverless. Run
`migrate metadata --target-type ELASTICSEARCH_SERVERLESS` (or the
standalone `migrate sanitize`) to strip the forbidden keys before
pre-creating indices. See
[METADATA_MIGRATION.md](METADATA_MIGRATION.md#serverless).

## Data path: S3 staging (Path D)

### `s3:GetObject` AccessDenied during load

The loader's IAM role doesn't have read access to the staging bucket.
Common causes:

- Bucket has a default-deny resource policy. Add an `Allow` for the
  loader role's ARN.
- S3 SSE-KMS encryption uses a CMK without `kms:Decrypt` granted to
  the loader role.
- Cross-account ferry: the destination-side role needs explicit access
  on the *source* bucket and the source-side role needs `s3:PutObject`
  with `bucket-owner-full-control` ACL.

### Loader stalls then exits 4 with `EXIT_DOC_ERRORS`

Per-document failures exceeded the threshold without DLQ. Two common
patterns:

- Mapping conflict on the destination — fix mapping (run `migrate
  metadata` first) and re-run; the loader resumes from the manifest
  checkpoint.
- Some part files are corrupt (truncated NDJSON). Re-run
  `migrate s3-extract` with the same manifest URI; it rewrites failed
  parts.

### Extractor partial completion

`indices_failed > 0` in the manifest summary, exit `4`. Re-run with the
same `--s3-uri` and `--manifest`. The extractor is idempotent and
resumes from per-index checkpoints.

## Data path: RFS (Path E)

### Container exits 0 but no documents arrive

The destination index name pattern in `--index-rename` matches nothing,
or the snapshot has only `.kibana*` / `.opendistro*` system indices. Run
the upstream RFS container with `--log-level DEBUG` and check
"Number of work items completed".

### `Could not find index template ...`

RFS reads index settings from the snapshot manifest, not from running
templates. If a source template was applied to indices that were then
snapshotted, the resolved settings are baked into the snapshot. If a
template was never applied, indices will inherit destination defaults.
This is RFS's behaviour, not a bug; run `migrate metadata` before RFS
if you also want the templates themselves on the destination.

### `block-rfs` in compat-check

The flagged indices use k-NN or an OpenSearch-only codec. RFS cannot
process them. Either:

- Migrate the clean indices via RFS and the flagged ones via Path D
  (extract by `--include` glob), then re-embed on the destination, or
- Skip RFS entirely and use Path D for the whole batch.

## Data path: Logstash (Path B)

### `version_conflict_engine_exception` even after removing `_version`

Logstash's `elasticsearch` output is still in `version_type => external`
mode somewhere. Check every output block in your pipeline; the toolkit's
[hardened filter](../Logstash_input/README.md) intentionally omits
`version` / `version_type` so the destination assigns its own.

### Logstash output index has the wrong name

The OpenSearch input plugin promotes `_index` into the event but does
**not** set it on the output by default. Make sure your output block
uses `index => "%{[@metadata][_index]}"` (preserves source name) or a
literal name, not the implicit one.

### `dropped` documents in pipeline metrics

`pipeline.workers > 1` plus `pipeline.ordered => false` reorders events
across the queue. Combined with `version_type => internal` (the
default), out-of-order updates for the same `_id` cause silent
last-write-wins. For per-`_id` ordering set `pipeline.workers => 1` and
`pipeline.ordered => true`. See [RUNBOOK.md](../RUNBOOK.md#ordering).

## Validation / cutover gates

### `validate_migration.py` count mismatch

- Refresh interval: source `_count` is taken at probe time; if writes
  are still happening on the source, the destination will be behind by
  the in-flight queue. Halt source writes (or accept dual-write
  window) and re-run.
- `refresh_interval: -1` left set on the destination after bulk load.
  Search-visible count lags ingestion. Set
  `refresh_interval: 1s` and `?refresh=true` on a small write to force
  a refresh before validating.
- Delete-by-query tombstones counted on one side and not the other.
  Use `--include-deleted` semantics consciously; for migrations,
  prefer reindexing into a fresh destination so tombstones don't
  travel.

### `shadow_diff` reports drift on a query that should match

Common false-positives:

- Source has a `function_score` / random score that varies per request.
  Add `seed` or use a deterministic query.
- Date-relative queries (`now-1d`) executed seconds apart on the two
  clusters return different windows. Pin to absolute timestamps in
  cutover queries.
- Mappings differ subtly (a field is `keyword` on one side and `text` on
  the other). Fix mapping or accept the drift category by raising
  `--topk-id-threshold`.

See [SHADOW_DIFF.md](SHADOW_DIFF.md).

### Replayer exits 4 with "response drift"

Status codes match, sizes differ outside the tolerance band. Usually
caused by:

- A `_search` response containing a `took` field that varies and isn't
  excluded by the canonical-JSON hash. Adjust the comparator config.
- The source response contains OpenSearch-specific fields the
  destination doesn't (e.g. `_clusters` only on cross-cluster queries).
  Filter the comparison or accept the difference.

See [CAPTURE_REPLAY.md](CAPTURE_REPLAY.md).

## Performance

### Bulk load is slow

- Destination `refresh_interval` should be `-1` and
  `number_of_replicas` should be `0` for the bulk window. Restore both
  after load (see [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md)).
- Bulk size too small. The S3 loader targets 5–10 MB compressed bulk
  bodies; if you see 1 KB bulks, the source documents are tiny and the
  loader is bottlenecked on round-trip latency rather than CPU. Raise
  `--bulk-batch-bytes`.
- Bulk size too large. `429` rejections from Elastic indicate the bulk
  threadpool can't keep up. Lower `--bulk-concurrency` or
  `--bulk-batch-bytes`.

### Source scroll throws `search_context_missing_exception`

Scroll TTL exceeded. The extractor refreshes TTL on each page; if you
see this anyway, the source cluster's `max_scroll_time` is shorter than
the request. Lower `--scroll-page-size` so each page completes faster.

### Migration compute (Fargate) keeps OOM-ing

Bulk decompression + JSON parsing is memory-hungry for very wide
documents (deeply nested or many fields). Raise the task memory
allocation in [`iac/terraform/rfs-fargate`](../iac/terraform/rfs-fargate/)
or run on a larger instance. The loader processes one part file at a
time, so peak memory is dominated by one part + retries.

## Tooling and CI

### `migrate compat-check` exits 4 in CI but the report looks fine

Exit `4` means *some* compatibility issue was found, even if it's just
"this index has a forbidden Serverless setting we'll strip". That is
intentional — the operator should look at the report. If you only want
to fail CI on `block-rfs` severity, parse the JSON report yourself with
`jq '.summary.block_rfs > 0'` and ignore the exit code.

### Tests pass locally but fail in CI

CI runs on a clean Python venv. The most common drift is
`pip install -e .` not being part of CI but being part of your local
setup, which means tests referencing `migrate` console-script behaviour
differ. Run `python -m pytest` (not `pytest`) to mirror CI.

## Where to go for help

- **Behaviour question:** [RUNBOOK.md](../RUNBOOK.md) for per-path
  operational sequences.
- **Architecture question:** [ARCHITECTURE.md](ARCHITECTURE.md) for the
  diagrams.
- **Tool flag question:** `migrate <subcommand> --help` or
  [TOOLS.md](TOOLS.md).
- **Network question:** [NETWORK_TOPOLOGY.md](NETWORK_TOPOLOGY.md).
- **Version question:** [VERSION_MATRIX.md](VERSION_MATRIX.md) and
  [COMPAT_CHECK.md](COMPAT_CHECK.md).
