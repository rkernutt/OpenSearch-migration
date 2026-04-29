variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type        = string
  description = "Resource name prefix; pair with the rfs-fargate module's prefix for clarity."
  default     = "os-rfs"
}

variable "cluster_arn" {
  type        = string
  description = "ECS cluster ARN. Pass the rfs-fargate module's `cluster_arn` output."
}

variable "task_definition_arn" {
  type        = string
  description = "RFS task definition ARN. Pass the rfs-fargate module's `task_definition_arn` output."
}

variable "passable_role_arns" {
  type        = list(string)
  description = "Task and task-execution role ARNs that Step Functions is allowed to PassRole to ECS. Typically the two roles created by rfs-fargate."
}

variable "subnet_ids" {
  type        = list(string)
  description = "Subnets for the Fargate task ENIs (private recommended)."
}

variable "security_group_id" {
  type        = string
  description = "Security group attached to the task ENIs. Use the rfs-fargate module's `security_group_id` output."
}

variable "assign_public_ip" {
  type    = bool
  default = false
}

variable "max_concurrency" {
  type        = number
  default     = 4
  description = "Maximum parallel RFS workers in the Map state."
}

variable "max_retries" {
  type        = number
  default     = 2
  description = "Retry attempts per worker on States.TaskFailed."
}

variable "log_retention_days" {
  type    = number
  default = 14
}
