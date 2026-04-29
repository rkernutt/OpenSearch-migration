# `rfs-orchestration` — parallel RFS workers via Step Functions

Sketch Terraform module that fans out the RFS Fargate task created by
[`iac/terraform/rfs-fargate/`](../rfs-fargate/) into N parallel workers
using an AWS Step Functions Map state.

Pair with [`s3_migration.rfs_runner`](../../../s3_migration/rfs_runner.py)
for local single-task runs and the rfs-fargate module for the underlying
container infrastructure.

## What it creates

- A Step Functions Standard state machine with a Map state that calls
  `ecs:runTask.sync` for each worker.
- An IAM role for Step Functions with `ecs:RunTask`, `ecs:DescribeTasks`,
  scoped `iam:PassRole` (only to the two roles created by `rfs-fargate`),
  and the events / logs permissions Step Functions needs.
- A CloudWatch log group for state machine execution history.

## Why it works without explicit work distribution

Upstream OpenSearch Migrations RFS coordinates parallel workers via a
shared **`migrations_working_state`** index on the **destination** cluster.
All workers point at the same target host, the same snapshot repository,
and the same target type — they self-assign work via that index. So this
module does not need to slice the input or pass a worker ID for
correctness; it only needs to launch N copies of the same task.

The state machine input shape is just:

```json
{ "workers": [{"id": 0}, {"id": 1}, {"id": 2}, {"id": 3}] }
```

Each item gets passed through to `RFS_WORKER_ID` for log tagging.

## Quick start

```bash
cd iac/terraform/rfs-fargate
terraform apply              # creates the cluster, task definition, IAM, sg

cd ../rfs-orchestration
cp terraform.tfvars.example terraform.tfvars

# Wire outputs from rfs-fargate
$EDITOR terraform.tfvars     # paste cluster_arn, task_definition_arn, security_group_id, role ARNs

terraform init
terraform validate
terraform plan
terraform apply
```

Trigger a run:

```bash
aws stepfunctions start-execution \
  --state-machine-arn "$(terraform output -raw state_machine_arn)" \
  --input '{"workers":[{"id":0},{"id":1},{"id":2},{"id":3}]}'
```

The state machine reaches **Succeeded** when every worker reports
`STOPPED` with exit code 0. Validate from any host:

```bash
python validate_migration.py \
  --indices "logs-2024,metrics-2024" \
  --check-existence --sample-size 50 \
  --strict-exit-codes --output-format json
```

## When *not* to use it

- For a single-task migration. The standalone `rfs-fargate` module + a
  manual `aws ecs run-task` (or the local `rfs_runner.py` wrapper) is
  simpler.
- When you don't want destination-side coordination state. The wrapper
  uses upstream's `migrations_working_state` index on the target; if you
  need that index renamed (e.g. for Serverless), upstream handles it
  automatically when `--target-type ELASTICSEARCH_SERVERLESS` is set on
  the underlying task definition.

## Tuning

| Variable | Default | Notes |
|----------|---------|-------|
| `max_concurrency` | 4 | Map state concurrency cap. Match to the destination's ingest capacity, not your CPU. |
| `max_retries` | 2 | Worker-level retries on `States.TaskFailed`. Upstream RFS is idempotent at the segment level so safe to retry. |
| `assign_public_ip` | false | Only set true if you have no NAT gateway and your subnets are public. |

## Notes

- **Standard, not Express, state machine.** RFS jobs typically run for
  many minutes; Express has a 5-minute hard limit.
- **Log group is `/aws/vendedlogs/states/...`** — the conventional path
  for Step Functions vended logging.
- **No EventBridge schedule** is provisioned. Add one out-of-band when
  you have a rollover snapshot cadence.
