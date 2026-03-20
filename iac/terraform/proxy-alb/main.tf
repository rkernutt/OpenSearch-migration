# Sketch only — attach real targets (EC2/ASG or ECS) separately.
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

resource "aws_security_group" "alb" {
  name_prefix = "${var.project}-alb-"
  vpc_id      = var.vpc_id
  description = "ALB fronting OpenSearch proxy"

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ingress_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "proxy_targets" {
  name_prefix = "${var.project}-proxy-"
  vpc_id      = var.vpc_id
  description = "Proxy tasks/instances — restrict to ALB only"

  ingress {
    from_port       = var.proxy_port
    to_port         = var.proxy_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "proxy" {
  name               = "${var.project}-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids
}

resource "aws_lb_target_group" "proxy" {
  name_prefix = "prx-"
  port        = var.proxy_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  # Targets (EC2 instance ids / ECS IPs) must be registered separately or via ASG attachment.
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.proxy.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.proxy.arn
  }
}

output "alb_dns_name" {
  value       = aws_lb.proxy.dns_name
  description = "Use this host (with :443) in Elastic reindex.remote.whitelist after DNS/HTTPS verified."
}

output "proxy_target_security_group_id" {
  value       = aws_security_group.proxy_targets.id
  description = "Attach to EC2/ENI or ECS service so only the ALB can reach the proxy port."
}
