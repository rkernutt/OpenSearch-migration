# Run the upstream OpenSearch Migrations RFS image as one or more Fargate tasks.
#
# Sketch only — the user supplies a pinned image (var.rfs_image), the snapshot
# bucket (var.snapshot_bucket), and the destination Elastic API key via Secrets
# Manager (var.target_api_key_secret_arn). All RFS flags are wired through
# container env vars so secrets never appear in the task definition's plain
# command. Pair with `s3_migration.rfs_runner` for local invocation, or use
# this module to scale parallel workers on AWS.

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

# ---------------------------------------------------------------------------
# Cluster (optional — pass an existing cluster ARN via var.existing_cluster_arn
# to skip creation).
# ---------------------------------------------------------------------------

resource "aws_ecs_cluster" "this" {
  count = var.existing_cluster_arn == null ? 1 : 0
  name  = "${var.project}-rfs"
}

locals {
  cluster_arn = var.existing_cluster_arn != null ? var.existing_cluster_arn : aws_ecs_cluster.this[0].arn
}

# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "rfs" {
  name              = "/ecs/${var.project}-rfs"
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

resource "aws_security_group" "rfs" {
  name_prefix = "${var.project}-rfs-"
  vpc_id      = var.vpc_id
  description = "RFS Fargate workers — egress only (S3 + Elastic Cloud)."

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All egress; tighten to S3 + Elastic Cloud CIDRs in production."
  }
}

# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${var.project}-rfs-execution"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  name = "${var.project}-rfs-execution-secrets"
  role = aws_iam_role.task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.target_api_key_secret_arn
      }
    ]
  })
}

resource "aws_iam_role" "task" {
  name               = "${var.project}-rfs-task"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

# Read-only on the snapshot bucket. Tighten the prefix to your actual snapshot
# layout for least privilege.
resource "aws_iam_role_policy" "task_s3_read" {
  name = "${var.project}-rfs-task-s3"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
        ]
        Resource = [
          "arn:aws:s3:::${var.snapshot_bucket}",
          "arn:aws:s3:::${var.snapshot_bucket}/${var.snapshot_prefix}*",
        ]
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# Task definition
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "rfs" {
  family                   = "${var.project}-rfs"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "rfs"
      image     = var.rfs_image
      essential = true
      command = [
        "./gradlew",
        var.gradle_task,
        "--args=${join(" ", local.rfs_args)}",
      ]
      environment = [
        { name = "AWS_REGION", value = var.aws_region },
      ]
      secrets = [
        {
          name      = "TARGET_API_KEY"
          valueFrom = var.target_api_key_secret_arn
        },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.rfs.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "rfs"
        }
      }
    }
  ])
}

locals {
  rfs_args = [
    "--snapshot-name", var.snapshot_name,
    "--s3-repo-uri", "s3://${var.snapshot_bucket}/${var.snapshot_prefix}",
    "--s3-region", var.aws_region,
    "--s3-local-dir", "/tmp/s3_files",
    "--lucene-dir", "/tmp/lucene_files",
    "--source-version", var.source_version,
    "--target-host", var.target_host,
    "--target-type", var.target_type,
    "--target-api-key", "$TARGET_API_KEY",
  ]
}

# ---------------------------------------------------------------------------
# Service (optional). Set var.task_count = 0 to manage runs out-of-band via
# `aws ecs run-task` — useful for one-off migrations.
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "rfs" {
  count           = var.task_count > 0 ? 1 : 0
  name            = "${var.project}-rfs"
  cluster         = local.cluster_arn
  task_definition = aws_ecs_task_definition.rfs.arn
  desired_count   = var.task_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.rfs.id]
    assign_public_ip = var.assign_public_ip
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
}
