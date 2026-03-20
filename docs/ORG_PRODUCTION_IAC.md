# Production hardening: Terraform and AWS networking

Turn the **sketches** in [`iac/`](../iac/) into production modules using your org’s standards. This guide lists the main controls and where they appear in code.

## 1. IAM: least privilege to OpenSearch

- **Task role** (proxy calls OpenSearch with SigV4): scope `es:ESHttpGet`, `es:ESHttpHead`, `es:ESHttpPost` to your **domain ARN(s)** instead of `*`.
- In [`proxy-ecs`](../iac/terraform/proxy-ecs), set `opensearch_domain_arns = ["arn:aws:es:region:account:domain/name/*"]` (tune path per [AWS docs](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/ac.html)).
- Fine-grained access inside the domain is **separate** (FGAC user/role the proxy assumes via IAM).

## 2. Secrets: no passwords in Terraform state

- Prefer **AWS Secrets Manager** (or SSM Parameter Store **SecureString**) for `PROXY_PASSWORD`.
- Set `proxy_password_secret_arn` on the ECS module; leave `proxy_password` empty if your Terraform version supports the validation block.
- Grant the **task execution role** `secretsmanager:GetSecretValue` on that ARN only.
- Rotate secrets on a **cadence** aligned with [TLS_AND_CREDENTIAL_LIFECYCLE.md](TLS_AND_CREDENTIAL_LIFECYCLE.md).

## 3. VPC endpoints (private ECS without NAT)

If Fargate tasks have **no public IP**, provide egress without a NAT gateway where possible:

| Need | Endpoint type | Notes |
|------|----------------|--------|
| ECR pull | Interface: `com.amazonaws.region.ecr.api`, `ecr.dkr` | Or NAT |
| Logs | Interface: `com.amazonaws.region.logs` | For `awslogs` driver |
| S3 (ECR layers) | **Gateway** on route tables | S3 gateway endpoint |

Subnet and security group rules must allow HTTPS to these endpoints.

## 4. WAF on the public ALB

- Set `enable_waf = true` on [`proxy-alb`](../iac/terraform/proxy-alb) to attach a **Regional** Web ACL with a managed rule group (e.g. Core Rule Set).
- Add IP allowlists, geo rules, or rate-based rules per your security team.
- Tune **size restrictions** so legitimate `_search` payloads from Elastic reindex are not blocked (test with a canary).

## 5. TLS and cipher policy

- ALB listener uses a **current** `ssl_policy` (see [`main.tf`](../iac/terraform/proxy-alb/main.tf)); review quarterly with security.
- **ACM** certificate renewal is automatic; ensure DNS validation records are owned by your team.

## 6. ECS autoscaling and resilience

- Enable `enable_ecs_autoscaling` on **proxy-ecs** for CPU-based target tracking (adjust min/max per SLO).
- Health checks: ALB → target group **HTTP** check on port 9200; align timeout with Proxy startup.
- **Multi-AZ**: spread tasks across subnets; `desired_count` ≥ 2 for HA during deploys.

## 7. Observability

- **CloudWatch** log retention: raise or ship to your log platform; consider **Container Insights**.
- Metrics: ALB `TargetResponseTime`, `HTTPCode_Target_5XX_Count`, ECS CPU/memory.

## 8. Change control

- Treat allowlist changes on Elastic (`reindex.remote.whitelist`) and **proxy basic auth** as linked changes: document in your change system and test in non-prod first.

## Related docs

- [Proxy/README.md](../Proxy/README.md) — application behavior
- [RUNBOOK.md](../RUNBOOK.md) — migration flow
- [TLS_AND_CREDENTIAL_LIFECYCLE.md](TLS_AND_CREDENTIAL_LIFECYCLE.md) — rotation and reviews
