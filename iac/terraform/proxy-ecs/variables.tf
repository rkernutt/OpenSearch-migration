variable "aws_region" {
  type        = string
  description = "Region for ECS and ECR image."
  default     = "us-east-1"
}

variable "project" {
  type        = string
  default     = "os-proxy"
}

variable "vpc_id" {
  type        = string
}

variable "private_subnet_ids" {
  type        = list(string)
  description = "Subnets for Fargate tasks (must reach OpenSearch)."
}

variable "proxy_image" {
  type        = string
  description = "ECR URI for Proxy image (see Proxy/Dockerfile)."
}

variable "target_group_arn" {
  type        = string
  description = "ALB target group ARN (HTTP target port 9200)."
}

variable "alb_security_group_id" {
  type        = string
  description = "Security group attached to the ALB (tasks only accept 9200 from this SG)."
}

variable "opensearch_endpoint" {
  type        = string
  description = "OpenSearch domain URL, e.g. https://search-xxx.us-east-1.es.amazonaws.com"
}

variable "proxy_user" {
  type        = string
  description = "Basic auth user for Elastic remote reindex (plaintext in state—use Secrets Manager in production)."
  default     = "proxyuser"
}

variable "proxy_password" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Basic auth password (omit if using proxy_password_secret_arn)."
}

variable "proxy_password_secret_arn" {
  type        = string
  default     = ""
  description = "Secrets Manager ARN for PROXY_PASSWORD (plain string secret or JSON key per ECS valueFrom syntax)."
}

variable "opensearch_domain_arns" {
  type        = list(string)
  default     = []
  description = "OpenSearch domain ARNs for es:ESHttp* (empty = '*' — dev only)."
}

variable "desired_count" {
  type        = number
  default     = 2
}

variable "cpu" {
  type        = number
  default     = 256
}

variable "memory" {
  type        = number
  default     = 512
}

variable "enable_ecs_autoscaling" {
  type        = bool
  default     = false
  description = "CPU target tracking on ECS service desired count."
}

variable "ecs_min_capacity" {
  type    = number
  default = 2
}

variable "ecs_max_capacity" {
  type    = number
  default = 8
}

variable "ecs_target_cpu_percent" {
  type        = number
  default     = 70
  description = "Target average CPU for Application Auto Scaling."
}
