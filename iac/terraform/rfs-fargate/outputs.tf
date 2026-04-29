output "cluster_arn" {
  description = "ECS cluster ARN used by the RFS task (created or reused)."
  value       = local.cluster_arn
}

output "task_definition_arn" {
  description = "ARN of the RFS task definition. Pass this to the rfs-orchestration module to fan out parallel workers."
  value       = aws_ecs_task_definition.rfs.arn
}

output "task_definition_family" {
  description = "Family name (without revision) of the RFS task definition."
  value       = aws_ecs_task_definition.rfs.family
}

output "security_group_id" {
  description = "Security group attached to RFS task ENIs."
  value       = aws_security_group.rfs.id
}

output "log_group_name" {
  description = "CloudWatch Logs group receiving RFS task output."
  value       = aws_cloudwatch_log_group.rfs.name
}
