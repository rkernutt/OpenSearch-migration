# ALB in front of OpenSearch SigV4 proxy (sketch)

**Not a complete drop-in.** Fill in variables and add missing pieces (ECS/EC2 service, IAM, logging) before production use.

## What this illustrates

1. **Application Load Balancer** with **HTTPS** listener (port 443).
2. **Target group** type `instance` or `ip` (for ECS), **HTTP** port **9200** → your proxy containers/instances.
3. **Security groups** (split ALB vs targets).

## Layout

- Copy `terraform.tfvars.example` → `terraform.tfvars` and edit.
- Run `terraform init` / `plan` / `apply` in this directory after connecting providers.

## Files

- `main.tf` — ALB, listener, target group, coarse SG rules (review!).
- `waf.tf` — optional Regional WAFv2 (`enable_waf = true`).
- `variables.tf` — inputs you must supply.

## Relationship to Elastic Cloud remote reindex

1. Complete a TLS listener with a **public ACM cert** on the ALB.
2. Set proxy **basic auth** (`PROXY_USER` / `PROXY_PASSWORD`) on the proxy tier.
3. Add the ALB DNS name:443 to **`reindex.remote.whitelist`** on Elastic Cloud Hosted.
4. Use `https://<alb-dns>/` plus basic auth as the remote reindex `source.remote` host and credentials.

See also [Proxy/README.md](../../../Proxy/README.md). To run the proxy on **Fargate**, continue with [proxy-ecs](../proxy-ecs/README.md).
