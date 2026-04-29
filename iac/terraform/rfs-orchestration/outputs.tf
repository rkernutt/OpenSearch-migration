output "state_machine_arn" {
  description = "ARN of the Step Functions state machine that fans RFS workers out in parallel."
  value       = aws_sfn_state_machine.rfs.arn
}

output "state_machine_name" {
  value = aws_sfn_state_machine.rfs.name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.sfn.name
}
