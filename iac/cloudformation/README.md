# CloudFormation: VPC Proxy for Private OpenSearch Migration

## What This Stack Does

Amazon OpenSearch Service domains deployed inside a private VPC (with no internet egress) cannot reach Elastic Cloud directly. This CloudFormation stack deploys a lightweight nginx reverse proxy inside that VPC which:

- Listens on **port 9200** (TCP) — the standard Elasticsearch API port
- Forwards all requests over HTTPS to your **Elastic Cloud endpoint**
- Is reachable only from within the VPC (internal Network Load Balancer)
- Requires **no SSH access** — management is via AWS Systems Manager Session Manager
- Ships nginx access/error logs and basic host metrics to **CloudWatch**

### Architecture

```
OpenSearch Migration Tool (inside VPC)
        │
        │  HTTP :9200
        ▼
 Internal Network Load Balancer
        │
        │  TCP :9200
        ▼
  EC2 nginx proxy (AutoScaling Group, size 1)
        │
        │  HTTPS (TLS 1.2/1.3) via NAT Gateway
        ▼
  Elastic Cloud Elasticsearch endpoint
```

The proxy instance runs Amazon Linux 2023 and is replaced automatically by the Auto Scaling Group if it becomes unhealthy.

---

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `VpcId` | `AWS::EC2::VPC::Id` | — | VPC to deploy the proxy into. Must be the same VPC as your OpenSearch domain. |
| `SubnetId` | `AWS::EC2::Subnet::Id` | — | Private subnet for the EC2 instance. **Must have a NAT gateway** for outbound internet access to reach Elastic Cloud. |
| `ElasticCloudEndpoint` | `String` | — | Full Elastic Cloud URL including scheme and port. Example: `https://my-deployment.es.us-east-1.aws.elastic-cloud.com:9243` |
| `InstanceType` | `String` | `t3.small` | EC2 instance type. `t3.small` is sufficient for most migration workloads. |
| `KeyPairName` | `String` | *(empty)* | Optional EC2 Key Pair for direct SSH access. Leave blank to use SSM Session Manager only (recommended). |
| `AllowedCidr` | `String` | `10.0.0.0/8` | CIDR range allowed to connect to the proxy on port 9200. Restrict to your migration subnet CIDR for tighter security. |

---

## Prerequisites

1. An AWS VPC containing your Amazon OpenSearch Service domain.
2. A **private subnet** in that VPC with a NAT gateway providing outbound internet access.
3. Your **Elastic Cloud deployment URL** (found in the Elastic Cloud console under *Manage deployment → Copy endpoint*).
4. AWS CLI configured with permissions to create EC2, IAM, ELB, and AutoScaling resources.

---

## Deploy

### Using the AWS CLI

```bash
aws cloudformation deploy \
  --stack-name opensearch-elastic-proxy \
  --template-file iac/cloudformation/vpc-proxy.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      VpcId=vpc-0123456789abcdef0 \
      SubnetId=subnet-0123456789abcdef0 \
      ElasticCloudEndpoint=https://my-deployment.es.us-east-1.aws.elastic-cloud.com:9243 \
      InstanceType=t3.small \
      AllowedCidr=10.10.0.0/16
```

Optionally include a Key Pair for SSH:

```bash
      KeyPairName=my-key-pair \
```

### Using the AWS Console

1. Open **CloudFormation → Create stack → With new resources**.
2. Upload `iac/cloudformation/vpc-proxy.yaml`.
3. Fill in the parameters and click through to create.
4. Tick **I acknowledge that AWS CloudFormation might create IAM resources with custom names**.

### Retrieving the Proxy Endpoint

After the stack reaches `CREATE_COMPLETE`, retrieve the proxy endpoint:

```bash
aws cloudformation describe-stacks \
  --stack-name opensearch-elastic-proxy \
  --query 'Stacks[0].Outputs'
```

Or from the console: **CloudFormation → Stacks → opensearch-elastic-proxy → Outputs**.

The `ProxyEndpoint` output will look like:

```
http://opensearch-elastic-proxy-nlb-<id>.<region>.elb.amazonaws.com:9200
```

---

## Using the Proxy with the Migration GUI

The proxy endpoint is a drop-in replacement for a direct Elastic Cloud URL anywhere the migration tool accepts a **target Elasticsearch URL**.

### In the Migration GUI

1. Open the migration GUI (typically at `http://localhost:3000` or your deployed URL).
2. In the **Target cluster** configuration, enter:
   - **URL**: `http://<ProxyEndpoint value>` (from the stack output)
   - **Username**: your Elastic Cloud username or API key ID
   - **Password**: your Elastic Cloud password or API key secret
3. Run a connection test — the GUI will talk to nginx on port 9200 and nginx will forward the authenticated request to Elastic Cloud over HTTPS.

### With curl (for verification)

From any host inside the VPC:

```bash
# Replace with your ProxyEndpoint output value
PROXY=http://opensearch-elastic-proxy-nlb-xxxx.us-east-1.elb.amazonaws.com:9200

# Test connectivity (unauthenticated — should return a 401 from Elastic Cloud)
curl -v $PROXY/

# Test with credentials
curl -u "username:password" $PROXY/_cluster/health?pretty
```

### With the Logstash pipeline

If you are using the Logstash Docker configuration in this repo, set the `output.elasticsearch.hosts` to the proxy endpoint:

```yaml
output {
  elasticsearch {
    hosts => ["http://opensearch-elastic-proxy-nlb-xxxx.us-east-1.elb.amazonaws.com:9200"]
    user  => "elastic"
    password => "${ELASTIC_PASSWORD}"
  }
}
```

---

## Monitoring

CloudWatch log groups are created automatically under:

| Log Group | Contents |
|-----------|----------|
| `/aws/ec2/<stack-name>/nginx-access` | nginx access log (one stream per instance) |
| `/aws/ec2/<stack-name>/nginx-error` | nginx error log |
| `/aws/ec2/<stack-name>/user-data` | Bootstrap script output (useful for troubleshooting) |

Custom metrics are published to the `<stack-name>/ProxyMetrics` CloudWatch namespace (CPU, memory, network).

To view bootstrap logs immediately after deployment:

```bash
aws logs tail /aws/ec2/opensearch-elastic-proxy/user-data --follow
```

---

## Troubleshooting

### Instance fails health checks

1. Check user-data logs: `aws logs tail /aws/ec2/<stack-name>/user-data`
2. Connect via SSM Session Manager:
   ```bash
   # Get the instance ID
   aws autoscaling describe-auto-scaling-groups \
     --auto-scaling-group-names opensearch-elastic-proxy-asg \
     --query 'AutoScalingGroups[0].Instances[0].InstanceId'

   aws ssm start-session --target i-0123456789abcdef0
   ```
3. Inside the session, check nginx status:
   ```bash
   sudo systemctl status nginx
   sudo nginx -t
   sudo journalctl -u nginx -n 50
   ```

### Connection refused / timeout from migration tool

- Confirm the migration tool is running in the same VPC or a peered VPC covered by `AllowedCidr`.
- Check the Security Group allows inbound TCP 9200 from the migration tool's subnet.
- Verify the EC2 instance is `InService` in the Target Group:
  ```bash
  aws elbv2 describe-target-health \
    --target-group-arn <TargetGroupArn from stack resources>
  ```

### nginx returns 502 Bad Gateway

- The proxy cannot reach Elastic Cloud. Check that the subnet's NAT gateway is working:
  ```bash
  # From an SSM session on the proxy instance
  curl -v https://my-deployment.es.us-east-1.aws.elastic-cloud.com:9243
  ```
- Verify `ElasticCloudEndpoint` is correct and includes the port.

---

## Clean Up

To delete all resources created by this stack:

```bash
aws cloudformation delete-stack --stack-name opensearch-elastic-proxy
```

Wait for deletion to complete:

```bash
aws cloudformation wait stack-delete-complete --stack-name opensearch-elastic-proxy
```

> **Note:** CloudWatch log groups are **not** deleted automatically. To remove them:
> ```bash
> aws logs delete-log-group --log-group-name /aws/ec2/opensearch-elastic-proxy/nginx-access
> aws logs delete-log-group --log-group-name /aws/ec2/opensearch-elastic-proxy/nginx-error
> aws logs delete-log-group --log-group-name /aws/ec2/opensearch-elastic-proxy/user-data
> ```

---

## Cost Estimate

For `us-east-1` (approximate, subject to change):

| Resource | Estimated cost |
|----------|---------------|
| t3.small EC2 (24/7) | ~$15/month |
| Network Load Balancer | ~$16/month + LCU charges |
| NAT Gateway data transfer | Varies by migration volume |
| CloudWatch logs ingestion | Varies (~$0.50/GB) |

The proxy is intended to be a temporary resource for the duration of the migration. Delete the stack once migration is complete to stop incurring costs.
