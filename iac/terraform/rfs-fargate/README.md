# `rfs-fargate` — run upstream RFS on AWS Fargate

Sketch Terraform module that runs the upstream OpenSearch Migrations
**Reindex-from-Snapshot (RFS)** image as a Fargate task. Pair with the
[`s3_migration.rfs_runner`](../../../s3_migration/rfs_runner.py) Python
wrapper for local invocation.

## What it creates

- (Optional) An ECS cluster, or reuses one via `existing_cluster_arn`.
- A CloudWatch Logs group with configurable retention.
- A security group (egress-only by default; tighten to S3 + Elastic Cloud
  CIDRs in production).
- Task execution role with `secretsmanager:GetSecretValue` on the destination
  API-key secret.
- Task role with read-only S3 access to the snapshot bucket / prefix.
- A Fargate task definition wired up to inject the destination API key from
  Secrets Manager as `TARGET_API_KEY`.
- An optional ECS service if `task_count > 0`. With `task_count = 0` (the
  default) you trigger runs out-of-band with `aws ecs run-task`, which is
  often the right shape for one-off migrations.

## What it does **not** create

- The snapshot itself — register the S3 repo on the OpenSearch source and run
  `_snapshot/<repo>/<snap>` ahead of time.
- The Secrets Manager secret holding the destination API key — create it once
  and pass the ARN.
- A Lambda / Step Functions wrapper. The Python `rfs_runner` is a simpler
  trigger surface; promote to Step Functions when you actually run multi-task
  fan-outs.

## Quick start

```bash
cd iac/terraform/rfs-fargate
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars
terraform init
terraform validate
terraform plan
terraform apply
```

To run a one-off task once the module is applied with `task_count = 0`:

```bash
aws ecs run-task \
  --cluster "$(terraform output -raw cluster_arn 2>/dev/null || echo os-rfs-rfs)" \
  --launch-type FARGATE \
  --task-definition os-rfs-rfs \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-aaaa,subnet-bbbb],securityGroups=[sg-...],assignPublicIp=DISABLED}"
```

## Notes

- **Pin `rfs_image`.** Use a digest (`@sha256:...`), not `latest`. RFS
  behaviour and CLI flags evolve.
- **One task per migration job in v1.** Multi-worker coordination (the
  upstream `migrations_working_state` index) requires running multiple tasks
  pointed at the same target host with the same job id; that's a follow-up.
- **Validate after.** Use `validate_migration.py` (the existing repo script)
  with the same destination credentials and the migrated indices, ideally
  with `--check-existence --sample-size 50`.
