# Sketch: Fargate tasks behind an existing ALB target group.
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

check "proxy_credential" {
  assert {
    condition     = var.proxy_password_secret_arn != "" || var.proxy_password != ""
    error_message = "Set proxy_password or proxy_password_secret_arn."
  }
}

resource "aws_ecs_cluster" "this" {
  name = "${var.project}-cluster"
}

data "aws_iam_policy_document" "task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "exec" {
  name_prefix        = "${var.project}-exec-"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

resource "aws_iam_role_policy_attachment" "exec_managed" {
  role       = aws_iam_role.exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name_prefix        = "${var.project}-task-"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

data "aws_iam_policy_document" "opensearch_http" {
  statement {
    sid    = "OpenSearchHttp"
    effect = "Allow"
    actions = [
      "es:ESHttpGet",
      "es:ESHttpHead",
      "es:ESHttpPost",
    ]
    resources = length(var.opensearch_domain_arns) > 0 ? var.opensearch_domain_arns : ["*"]
  }
}

resource "aws_iam_role_policy" "task_opensearch" {
  name_prefix = "os-http-"
  role        = aws_iam_role.task.id
  policy      = data.aws_iam_policy_document.opensearch_http.json
}

data "aws_iam_policy_document" "exec_secret" {
  count = var.proxy_password_secret_arn != "" ? 1 : 0
  statement {
    sid    = "GetProxyPassword"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [var.proxy_password_secret_arn]
  }
}

resource "aws_iam_role_policy" "exec_secret" {
  count  = var.proxy_password_secret_arn != "" ? 1 : 0
  name   = "${var.project}-exec-secret"
  role   = aws_iam_role.exec.id
  policy = data.aws_iam_policy_document.exec_secret[0].json
}

resource "aws_security_group" "task" {
  name_prefix = "${var.project}-task-"
  vpc_id      = var.vpc_id
  description = "Fargate proxy tasks"

  ingress {
    from_port       = 9200
    to_port         = 9200
    protocol        = "tcp"
    security_groups = [var.alb_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

locals {
  proxy_secrets = var.proxy_password_secret_arn != "" ? [
    {
      name      = "PROXY_PASSWORD"
      valueFrom = var.proxy_password_secret_arn
    }
  ] : []
  base_env = [
    { name = "OPENSEARCH_ENDPOINT", value = var.opensearch_endpoint },
    { name = "AWS_REGION", value = var.aws_region },
    { name = "PROXY_USER", value = var.proxy_user },
    { name = "PROXY_LISTEN", value = "0.0.0.0:9200" },
  ]
  password_env = var.proxy_password_secret_arn == "" ? [
    { name = "PROXY_PASSWORD", value = var.proxy_password }
  ] : []
  proxy_env = concat(local.base_env, local.password_env)
  container_def = merge(
    {
      name      = "proxy"
      image     = var.proxy_image
      essential = true
      portMappings = [
        {
          containerPort = 9200
          protocol      = "tcp"
        }
      ]
      environment = local.proxy_env
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.proxy.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "proxy"
        }
      }
    },
    length(local.proxy_secrets) > 0 ? { secrets = local.proxy_secrets } : {}
  )
}

resource "aws_ecs_task_definition" "proxy" {
  family                   = "${var.project}-proxy"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.exec.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([local.container_def])
}

resource "aws_cloudwatch_log_group" "proxy" {
  name_prefix       = "${var.project}-proxy-"
  retention_in_days = 14
}

resource "aws_ecs_service" "proxy" {
  name            = "${var.project}-svc"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.proxy.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "proxy"
    container_port   = 9200
  }
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "ecs_service_name" {
  value = aws_ecs_service.proxy.name
}

output "task_security_group_id" {
  value = aws_security_group.task.id
}
