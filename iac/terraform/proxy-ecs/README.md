# ECS Fargate service for the OpenSearch SigV4 proxy (sketch)

Pairs with [proxy-alb](../proxy-alb): pass the target group ARN from the ALB stack, deploy this in **private subnets** with a route to OpenSearch (VPC endpoint / peering / same VPC).

## What you must supply

- **Networking**: Private subnets need **NAT** (or **VPC endpoints** for ECR, CloudWatch Logs, and ECS) so tasks can pull the image and write logs.
- **Image**: Build [Proxy/Dockerfile](../../../Proxy/Dockerfile), push to ECR, set `var.proxy_image`.
- **OpenSearch endpoint** and **IAM**: The task role must allow `es:ESHttp*` (or narrower) on your domain ARN. Set env `OPENSEARCH_ENDPOINT` (and optional `AWS_REGION`) in the container definition.
- **Secrets**: Prefer AWS Secrets Manager for `PROXY_USER` / `PROXY_PASSWORD` and inject via `secrets` in the task definition (not shown—this sketch uses plain `environment` for clarity).

## Apply order

1. `proxy-alb` (or existing ALB + target group **empty**).
2. `proxy-ecs` — service registers tasks into the target group.
3. Health check: default ALB health check is `/` on 9200; ensure the proxy responds (or add a dedicated path in [Proxy/app.py](../../../Proxy/app.py) if you tighten it).

## Files

- `main.tf` — cluster, roles, task definition (env or Secrets Manager), service, task SG.
- `autoscaling.tf` — optional CPU target tracking (`enable_ecs_autoscaling`).
- `variables.tf` — VPC, subnets, image, OpenSearch endpoint, TG ARN, domain ARNs for IAM.

**Production:** set `opensearch_domain_arns`, use `proxy_password_secret_arn`, and read [ORG_PRODUCTION_IAC.md](../../../docs/ORG_PRODUCTION_IAC.md).

This is **not** production-hardened (no WAF, no Container Insights defaults, minimal CPU/memory). Extend per your org.
