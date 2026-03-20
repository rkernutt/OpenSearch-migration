# Infrastructure as code (examples)

This folder holds **starter examples** only—not production-ready modules. Adapt VPC IDs, subnets, certificates, and security groups to your organization.

| Path | Purpose |
|------|---------|
| [terraform/proxy-alb](terraform/proxy-alb) | Sketch for an internet-facing **Application Load Balancer** in front of the [Proxy](../Proxy/README.md) (HTTPS → HTTP targets on port 9200). |
| [terraform/proxy-ecs](terraform/proxy-ecs) | Sketch **Fargate** service that runs the proxy container and registers with that target group. |

Before applying Terraform:

- Create or identify VPC, **public subnets** for the ALB, **ACM certificate** in the same region.
- Run the proxy on EC2/ECS in private subnets that can reach **Amazon OpenSearch** (and that the ALB target group can reach).
- Lock down security groups: ALB allows `443` from trusted CIDRs; targets allow **only** the ALB SG on **9200**.

**Production checklist:** [docs/ORG_PRODUCTION_IAC.md](../docs/ORG_PRODUCTION_IAC.md) and [docs/TLS_AND_CREDENTIAL_LIFECYCLE.md](../docs/TLS_AND_CREDENTIAL_LIFECYCLE.md).

For Elastic Cloud IP allowlists when using a public ALB, see Elastic documentation for egress IP ranges (if published for your plan).
