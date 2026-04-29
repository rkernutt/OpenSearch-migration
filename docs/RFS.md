# Reindex-from-Snapshot via upstream (Path E)

Runbook for using the upstream OpenSearch Migrations
**Reindex-from-Snapshot (RFS)** tool to migrate from an S3 snapshot of an
OpenSearch / Elasticsearch cluster directly into Elastic Cloud (Hosted **or**
Serverless), without re-reading every document over HTTP from the source.

We **wrap** the upstream container rather than re-implementing it: RFS reads
S3-backed snapshot metadata and parses Lucene segments to extract documents,
which has no good pure-Python implementation.

> Looking for the document-streaming path that doesn't need a snapshot? See
> [S3_MIGRATION.md](S3_MIGRATION.md) (Path D).

## When to use this path

- You **already snapshot** OpenSearch / Elasticsearch to S3 as part of normal
  ops, or can run a one-off `_snapshot/<repo>/<snap>` ahead of the migration.
- The data volume is large (multi-hundred-GB to multi-TB) and you want to
  avoid the `_search` + `_bulk` round-trip overhead of Path D.
- Destination is **Elastic Cloud Hosted** or **Elastic Serverless**.

## When *not* to use this path

- You have no snapshot and don't want to take one. Use Path D instead.
- Source is OpenSearch **Serverless** — its snapshot model differs from
  provisioned domains and isn't covered by upstream RFS today.
- You need rich post-load comparison beyond counts + sampling. Combine RFS
  with `validate_migration.py`, or look at upstream Capture-and-Replay.

## Components

| Tool | What it does |
|------|--------------|
| Upstream RFS image (you pin) | Reads S3 snapshot, parses Lucene, bulk-indexes to target. |
| [`s3_migration.rfs_runner`](../s3_migration/rfs_runner.py) | Thin Python wrapper. Builds the container command, streams logs, auto-runs `validate_migration.py`. |
| [`iac/terraform/rfs-fargate/`](../iac/terraform/rfs-fargate/) | Sketch Terraform to run RFS as a Fargate task on AWS, with scoped IAM and Secrets Manager wiring. |
| [`iac/terraform/rfs-orchestration/`](../iac/terraform/rfs-orchestration/) | Step Functions Map state that fans the rfs-fargate task into N parallel workers (upstream RFS coordinates via `migrations_working_state` on the destination). |
| [`validate_migration.py`](../validate_migration.py) | Post-run reconciliation. |

## Prerequisites

1. **Source snapshot in S3.** Register an S3 repository on the source and
   take a snapshot (Amazon OpenSearch Service: register a manual S3 repo with
   an IAM role attached to the domain; self-managed: standard
   `repository-s3`).
2. **Upstream image.** Build or pull a pinned tag of
   [`opensearch-project/opensearch-migrations`](https://github.com/opensearch-project/opensearch-migrations)
   that exposes `DocumentsFromSnapshotMigration:run`. **Pin by digest**, not
   `latest`. Track the digest in your runbook.
3. **Destination access.** API key (preferred) or basic auth on the target
   Elastic deployment. For Serverless, create a scoped API key from the
   project's Management → API Keys page.
4. **Local container runtime.** `docker` (or `podman`) on PATH for local runs;
   Fargate / ECS for the Terraform path.

## Local quick start

```bash
export RFS_UPSTREAM_IMAGE="ghcr.io/your-org/opensearch-migrations@sha256:<digest>"

python -m s3_migration.rfs_runner \
  --upstream-image "$RFS_UPSTREAM_IMAGE" \
  --snapshot-name snap-2026-04-29 \
  --s3-repo-uri s3://my-os-snapshots/production/repo \
  --s3-region us-east-1 \
  --target-host "$DEST_ELASTIC_HOST" \
  --target-api-key "$DEST_ELASTIC_API_KEY" \
  --target-type ELASTICSEARCH_SERVERLESS \
  --source-version OpenSearch_2_13 \
  --indices-validate "logs-2024,metrics-2024" \
  --validate-sample-size 50 \
  --strict-exit-codes --log-format json
```

The wrapper streams the upstream container's stdout/stderr through the same
structured logger the rest of the toolkit uses. Secrets (`--target-api-key` /
`--target-password`) are passed in via container env vars and never appear on
the command line.

Add `--dry-run` to print the resolved container command (with secrets
redacted) without actually running it — useful in CI / preview pipelines.

### Auth

| Mode | Wrapper flag | Notes |
|------|--------------|-------|
| API key (recommended) | `--target-api-key` (or `DEST_ELASTIC_API_KEY` env) | Required for Elastic Serverless. |
| Basic auth | `--target-username` / `--target-password` | Hosted only; prefer API keys. |

### Why pin the image?

RFS CLI flags and on-snapshot behaviour evolve between upstream releases.
Pinning by digest ensures reproducibility and lets you upgrade
deliberately. Tracking the upgrade in your runbook (`Replaced
@sha256:<old>` → `@sha256:<new>` on YYYY-MM-DD) is enough.

## AWS Fargate quick start

The Terraform module
[`iac/terraform/rfs-fargate/`](../iac/terraform/rfs-fargate/) creates the
minimum machinery to run RFS as a Fargate task: cluster (or reuse one), task
role (S3 read on the snapshot bucket), execution role (Secrets Manager read
for the API key), task definition, and an optional service.

```bash
cd iac/terraform/rfs-fargate
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars   # set image digest, snapshot bucket, secret ARN, ...
terraform init
terraform validate
terraform plan
terraform apply
```

With `task_count = 0` (default) you trigger one-off runs out-of-band:

```bash
aws ecs run-task \
  --cluster os-rfs-rfs \
  --launch-type FARGATE \
  --task-definition os-rfs-rfs \
  --network-configuration \
    "awsvpcConfiguration={subnets=[subnet-aaaa,subnet-bbbb],securityGroups=[sg-...],assignPublicIp=DISABLED}"
```

Logs land in CloudWatch under `/ecs/os-rfs-rfs/...`.

## Elastic Serverless adaptations

The upstream `--target-type ELASTICSEARCH_SERVERLESS` flag does several
things we'd otherwise have to do by hand:

- **Settings sanitization.** Strips `index.number_of_shards`,
  `number_of_replicas`, `codec`, `translog.*`, `merge.*`, and
  `routing.allocation.*` from index settings before creating destination
  indices.
- **Hidden-index renaming.** The work-coordination index becomes
  `migrations_working_state` (no leading dot) so Serverless accepts it.
- **API restrictions.** Skips unsupported APIs (`/_cluster/settings`,
  `/_nodes`, …) instead of failing.

For Hosted, set `--target-type ELASTICSEARCH` and pre-create indices with
your own mapping if you want strict types. For OpenSearch destinations use
`--target-type OPENSEARCH`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Wrapper exit **2** "container runtime not found" | `docker` (or your `--container-cmd`) isn't on PATH. | Install Docker / Podman, or pass `--container-cmd /full/path`. |
| Wrapper exit **2** "upstream-image is required" | Forgot to pin the image. | Set `RFS_UPSTREAM_IMAGE` or pass `--upstream-image`. |
| RFS exits with `403` reading S3 | Missing IAM permission on the snapshot bucket. | Grant `s3:ListBucket` on the bucket and `s3:GetObject` on the prefix. |
| RFS exits with TLS / certificate errors hitting Elastic | Old Java cacerts in the image, or self-signed cluster. | Pin a newer image; for self-signed clusters mount a cert into the container and add `--container-arg -v $(pwd)/ca.pem:/etc/ssl/certs/ca-bundle.crt`. |
| `validate_migration.py` exit **1** afterwards | Real count mismatch. | Inspect failed indices in the JSON validate output; commonly a mapping conflict or skipped doc. |
| `validate_migration.py` exit **3** afterwards | Destination network/auth blip. | The wrapper exits **3** in this case. Re-run validation alone; if it passes, the migration is good. |
| Multiple parallel Fargate tasks fight each other | The work-coordination index is the same; that's intentional in upstream. | Make sure all tasks share the same job context (target host, snapshot, etc.). For v1 of the wrapper, prefer one task at a time. |

## See also

- [S3_MIGRATION.md](S3_MIGRATION.md) — Path D (NDJSON staging).
- [SERVERLESS.md](SERVERLESS.md) — Elastic Serverless destination notes.
- [AUTOMATION.md](AUTOMATION.md) — exit codes for `rfs_runner`.
- [RUNBOOK.md](../RUNBOOK.md) — full migration runbook with Path E.
- Upstream: [`opensearch-project/opensearch-migrations`](https://github.com/opensearch-project/opensearch-migrations)
- Adjacent fork (serverless-focused): [`m-adams/opensearch-to-elasticsearch-serverless`](https://github.com/m-adams/opensearch-to-elasticsearch-serverless)
