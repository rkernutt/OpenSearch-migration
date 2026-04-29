variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type        = string
  description = "Resource name prefix."
  default     = "os-rfs"
}

variable "vpc_id" {
  type        = string
  description = "VPC where the Fargate task ENIs live."
}

variable "subnet_ids" {
  type        = list(string)
  description = "Subnets for Fargate task ENIs (private subnets recommended; egress to S3 + Elastic Cloud required)."
}

variable "assign_public_ip" {
  type        = bool
  default     = false
  description = "Set true if subnets are public and you have no NAT gateway."
}

variable "existing_cluster_arn" {
  type        = string
  default     = null
  description = "Existing ECS cluster ARN to reuse. When null, a new cluster is created."
}

variable "rfs_image" {
  type        = string
  description = "Pinned upstream OpenSearch Migrations container image (RFS-capable). Avoid 'latest'."
}

variable "gradle_task" {
  type        = string
  default     = "DocumentsFromSnapshotMigration:run"
  description = "Gradle task to invoke inside the upstream image."
}

variable "snapshot_bucket" {
  type        = string
  description = "S3 bucket holding the OpenSearch snapshot repository."
}

variable "snapshot_prefix" {
  type        = string
  default     = ""
  description = "S3 key prefix (the snapshot repository root inside the bucket); include trailing slash if non-empty."
}

variable "snapshot_name" {
  type        = string
  description = "Snapshot name within the repository."
}

variable "source_version" {
  type        = string
  description = "Upstream --source-version (e.g. OpenSearch_2_13, Elasticsearch_7_10)."
}

variable "target_host" {
  type        = string
  description = "Destination Elasticsearch URL (Elastic Cloud Hosted or Serverless)."
}

variable "target_type" {
  type        = string
  default     = "ELASTICSEARCH_SERVERLESS"
  description = "Upstream --target-type."
  validation {
    condition     = contains(["ELASTICSEARCH", "ELASTICSEARCH_SERVERLESS", "OPENSEARCH"], var.target_type)
    error_message = "target_type must be one of ELASTICSEARCH, ELASTICSEARCH_SERVERLESS, OPENSEARCH."
  }
}

variable "target_api_key_secret_arn" {
  type        = string
  description = "Secrets Manager ARN holding the destination API key (the secret value is injected as TARGET_API_KEY)."
}

variable "task_cpu" {
  type    = string
  default = "2048"
}

variable "task_memory" {
  type    = string
  default = "8192"
}

variable "task_count" {
  type        = number
  default     = 0
  description = "Desired count for the optional ECS service. 0 = no service (run tasks out-of-band)."
}

variable "log_retention_days" {
  type    = number
  default = 14
}
