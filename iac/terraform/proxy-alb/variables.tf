variable "aws_region" {
  type        = string
  description = "Region for ALB and ACM cert."
  default     = "us-east-1"
}

variable "project" {
  type        = string
  description = "Name prefix for resources."
  default     = "os-proxy"
}

variable "vpc_id" {
  type        = string
  description = "VPC id where the ALB and targets live."
}

variable "public_subnet_ids" {
  type        = list(string)
  description = "At least two public subnets for the internet-facing ALB."
}

variable "certificate_arn" {
  type        = string
  description = "ACM certificate ARN for HTTPS (same region as ALB)."
}

variable "allowed_ingress_cidr" {
  type        = string
  description = "CIDR allowed to hit ALB:443 (use Elastic egress CIDRs if published, or 0.0.0.0/0 only with strong proxy auth)."
  default     = "0.0.0.0/0"
}

variable "proxy_port" {
  type        = number
  description = "Port the proxy listens on (PROJECT PROXY_LISTEN)."
  default     = 9200
}

variable "enable_waf" {
  type        = bool
  description = "Associate a Regional WAFv2 Web ACL (managed Core Rule Set) with the ALB."
  default     = false
}
