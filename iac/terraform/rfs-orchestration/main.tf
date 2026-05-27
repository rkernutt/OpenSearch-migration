# Step Functions orchestration for parallel RFS workers.
#
# Consumes the ECS task definition created by `iac/terraform/rfs-fargate/`
# and runs N copies of it in parallel. Upstream RFS coordinates between
# workers via a shared `migrations_working_state` index on the destination,
# so we just need to launch the tasks and wait.
#
# Out-of-band validation: run `validate_migration.py` from any host after
# the state machine reaches Succeeded. (We deliberately don't ship a
# validation Lambda here; a single subprocess call is simpler to operate.)

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${var.project}-rfs"
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.project}-rfs-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

# Permissions: run / wait on ECS tasks; pass the rfs-fargate task and execution
# roles into ECS; write SFN execution logs.
data "aws_iam_policy_document" "sfn_ecs" {
  statement {
    actions = [
      "ecs:RunTask",
      "ecs:StopTask",
      "ecs:DescribeTasks",
    ]
    resources = ["*"]
  }

  statement {
    actions   = ["iam:PassRole"]
    resources = var.passable_role_arns
  }

  statement {
    actions = [
      "events:PutTargets",
      "events:PutRule",
      "events:DescribeRule",
    ]
    resources = ["*"]
  }

  statement {
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sfn_ecs" {
  name   = "${var.project}-rfs-sfn-ecs"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn_ecs.json
}

# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

locals {
  # ASL definition — Map runs N parallel ECS RunTask.sync calls.
  state_machine_definition = jsonencode({
    Comment = "Parallel RFS workers (upstream OpenSearch Migrations)"
    StartAt = "RunWorkers"
    States = {
      RunWorkers = {
        Type           = "Map"
        ItemsPath      = "$.workers"
        MaxConcurrency = var.max_concurrency
        Iterator = {
          StartAt = "RunRFSTask"
          States = {
            RunRFSTask = {
              Type     = "Task"
              Resource = "arn:aws:states:::ecs:runTask.sync"
              Parameters = {
                LaunchType     = "FARGATE"
                Cluster        = var.cluster_arn
                TaskDefinition = var.task_definition_arn
                NetworkConfiguration = {
                  AwsvpcConfiguration = {
                    Subnets        = var.subnet_ids
                    SecurityGroups = [var.security_group_id]
                    AssignPublicIp = var.assign_public_ip ? "ENABLED" : "DISABLED"
                  }
                }
                # Each worker can override the env to e.g. tag itself for
                # logging without changing the task definition. Upstream RFS
                # coordinates by destination-side state, so this is mostly
                # cosmetic.
                Overrides = {
                  ContainerOverrides = [
                    {
                      "Name" = "rfs"
                      "Environment" = [
                        {
                          "Name"    = "RFS_WORKER_ID"
                          "Value.$" = "States.Format('{}', $.id)"
                        }
                      ]
                    }
                  ]
                }
              }
              Retry = [
                {
                  ErrorEquals     = ["States.TaskFailed"]
                  IntervalSeconds = 30
                  MaxAttempts     = var.max_retries
                  BackoffRate     = 2.0
                }
              ]
              End = true
            }
          }
        }
        End = true
      }
    }
  })
}

resource "aws_sfn_state_machine" "rfs" {
  name     = "${var.project}-rfs"
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  definition = local.state_machine_definition

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = false
    level                  = "ALL"
  }
}
