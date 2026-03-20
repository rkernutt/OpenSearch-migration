# Optional AWS WAF v2 (Regional) on the ALB — enable after testing with your reindex traffic patterns.

resource "aws_wafv2_web_acl" "proxy" {
  count = var.enable_waf ? 1 : 0
  name  = "${var.project}-proxy-alb"
  scope = "REGIONAL"

  default_action {
    allow {}
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project}-waf"
    sampled_requests_enabled   = true
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 10

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedRulesCommonRuleSet"
      sampled_requests_enabled   = true
    }
  }
}

resource "aws_wafv2_web_acl_association" "proxy_alb" {
  count        = var.enable_waf ? 1 : 0
  resource_arn = aws_lb.proxy.arn
  web_acl_arn  = aws_wafv2_web_acl.proxy[0].arn
}
