# IAM Permissions — Migration CloudFormation Stacks

This directory contains the least-privilege IAM policy and helper scripts
needed to deploy and operate the OpenSearch → Elastic migration
CloudFormation stacks.

---

## Quick start

```bash
# 1. Create the deployer policy in your AWS account
aws iam create-policy \
  --policy-name opensearch-migration-deployer \
  --policy-document file://iam/cloudformation-deployer-policy.json

# 2. Attach to your IAM user or role
aws iam attach-user-policy \
  --user-name <YOUR_IAM_USER> \
  --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/opensearch-migration-deployer

# 3. Pre-create secrets as SSM SecureStrings (recommended — see below)
./iam/pre-deploy-ssm-secrets.sh \
  --region us-east-1 \
  --elastic-url https://my-deployment.es.us-east-1.aws.elastic-cloud.com:9243 \
  --elastic-key <base64-api-key> \
  --os-password <opensearch-password>   # only if using basic auth

# 4. Deploy a stack
aws cloudformation deploy \
  --template-file iac/cloudformation/vpc-logstash.yaml \
  --stack-name opensearch-migration-logstash \
  --parameter-overrides ... \
  --capabilities CAPABILITY_IAM \
  --region us-east-1
```

---

## Files

| File | Purpose |
|---|---|
| `cloudformation-deployer-policy.json` | Least-privilege IAM policy for the entity running `aws cloudformation deploy` |
| `pre-deploy-ssm-secrets.sh` | Creates SSM SecureString parameters before stack deploy (recommended security practice) |

---

## IAM policy — permissions breakdown

The policy (`cloudformation-deployer-policy.json`) grants the minimum
permissions required to deploy **any** of the three migration stacks. All
resources are scoped as tightly as AWS allows.

### CloudFormation
| Action | Why needed |
|---|---|
| `CreateStack` / `UpdateStack` / `DeleteStack` | Deploy and tear down stacks |
| `CreateChangeSet` / `ExecuteChangeSet` | `aws cloudformation deploy` uses change sets internally |
| `DescribeStacks` / `DescribeStackEvents` | Monitor deployment progress |
| `ValidateTemplate` / `GetTemplateSummary` | Pre-flight validation |

**Resource scope:** `arn:aws:cloudformation:*:*:stack/opensearch-migration-*/*`
(stacks named `opensearch-migration-*` only)

### S3 (CloudFormation template upload)
`aws cloudformation deploy` automatically uploads larger templates to a
managed S3 bucket (`cf-templates-<hash>-<region>`).

**Resource scope:** `arn:aws:s3:::cf-templates-*`

### EC2
Scoped broadly to `*` because AWS does not support resource-level
restrictions on most EC2 mutating actions, but the actions are limited to
exactly what the stacks create:

| Action | Needed for |
|---|---|
| `CreateSecurityGroup` / `DeleteSecurityGroup` + rules | Inbound port rules for proxy/Logstash/Kafka |
| `CreateLaunchTemplate` | EC2 instance configuration |
| `RunInstances` | Auto Scaling Group launches instances |

### IAM
Creating an EC2 instance role requires `CAPABILITY_IAM`. Policy scope is
restricted to `opensearch-migration-*` role and instance-profile names:

| Action | Why needed |
|---|---|
| `CreateRole` / `AttachRolePolicy` / `PutRolePolicy` | EC2 instance role with SSM + CloudWatch policies |
| `CreateInstanceProfile` / `AddRoleToInstanceProfile` | Attach role to EC2 instance |
| `PassRole` | Allow EC2 service to assume the role |

`iam:PassRole` is further scoped by condition:
```json
"Condition": { "StringEquals": { "iam:PassedToService": "ec2.amazonaws.com" } }
```

### SSM Parameter Store
| Action | Why needed |
|---|---|
| `PutParameter` | Stack creates `/migration/*` parameters for secrets |
| `GetParameter` / `GetParameters` | CloudFormation resolves `{{resolve:ssm:...}}` references |
| `DeleteParameter` | Stack deletion removes parameters |

**Resource scope:**
- `/migration/*` — migration secrets
- `/aws/service/ami-amazon-linux-latest/*` — AWS public AMI lookup (read-only)

### Elastic Load Balancing
Required only for `vpc-proxy.yaml` (the nginx NLB stack).
Scoped to resources tagged `MigrationType: nginx-proxy | logstash | kafka`.

### CloudWatch Logs
Create and manage log groups under `/migration/*` only.

---

## What is stored in SSM Parameter Store?

### Current behaviour (default)

| Secret | Stored in SSM? | Type | Visible in AWS Console? |
|---|---|---|---|
| Elastic API Key | ✅ Yes — `/migration/elastic-api-key` | **String** (plaintext) | Yes — readable via SSM console / API |
| Elastic Endpoint URL | ❌ No | — | Embedded in EC2 UserData |
| OpenSearch Password | ❌ No | — | Embedded in EC2 UserData |

> **CloudFormation limitation:** `AWS::SSM::Parameter` cannot create
> `SecureString` type parameters. The `NoEcho: true` flag hides the value
> during deployment but the SSM parameter itself is stored unencrypted.

### Recommended approach — pre-seed SecureStrings

For production, run `pre-deploy-ssm-secrets.sh` **before** deploying the
stack. This creates KMS-encrypted `SecureString` parameters that the EC2
instance reads at boot time via the AWS CLI (`aws ssm get-parameter
--with-decryption`). The unencrypted value never touches CloudFormation
and is not visible in the AWS Console.

```
/migration/elastic-endpoint     SecureString  (KMS encrypted)
/migration/elastic-api-key      SecureString  (KMS encrypted)
/migration/kafka/elastic-api-key SecureString (KMS encrypted)
/migration/opensearch-password  SecureString  (KMS encrypted, basic auth only)
```

The shell script uses the default AWS-managed KMS key (`alias/aws/ssm`).
Pass `--kms-key alias/my-cmk` to use your own Customer Managed Key.

---

## Required permissions — matrix by stack

| Permission group | `vpc-proxy.yaml` | `vpc-logstash.yaml` | `vpc-kafka.yaml` |
|---|:---:|:---:|:---:|
| CloudFormation | ✅ | ✅ | ✅ |
| S3 (template upload) | ✅ | ✅ | ✅ |
| EC2 (SG + Launch Template) | ✅ | ✅ | ✅ |
| Auto Scaling | ✅ | ✅ | ✅ |
| IAM (EC2 role + profile) | ✅ | ✅ | ✅ |
| SSM Parameters (`/migration/*`) | ❌ | ✅ | ✅ |
| Elastic Load Balancing (NLB) | ✅ | ❌ | ❌ |
| CloudWatch Logs | ❌ | ✅ | ✅ |

The single `cloudformation-deployer-policy.json` covers all three. There
is no harm attaching it even if you only deploy one stack.

---

## Creating a dedicated IAM role (vs attaching to a user)

If you prefer a deployable role (e.g. for CI/CD or cross-account):

```bash
# 1. Create a trust policy (replace 123456789012 with your account ID)
cat > /tmp/trust.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::123456789012:root" },
    "Action": "sts:AssumeRole"
  }]
}
EOF

# 2. Create the role
aws iam create-role \
  --role-name opensearch-migration-deployer \
  --assume-role-policy-document file:///tmp/trust.json

# 3. Attach the policy
aws iam attach-role-policy \
  --role-name opensearch-migration-deployer \
  --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/opensearch-migration-deployer

# 4. Assume the role when deploying
aws sts assume-role \
  --role-arn arn:aws:iam::<ACCOUNT_ID>:role/opensearch-migration-deployer \
  --role-session-name migration-deploy
```

---

## Cleanup

```bash
# Delete SSM secrets after migration is complete
aws ssm delete-parameters --region <REGION> \
  --names \
    /migration/elastic-endpoint \
    /migration/elastic-api-key \
    /migration/kafka/elastic-api-key \
    /migration/opensearch-password

# Delete CloudFormation stacks
aws cloudformation delete-stack --stack-name opensearch-migration-logstash --region <REGION>
aws cloudformation delete-stack --stack-name opensearch-migration-kafka    --region <REGION>
aws cloudformation delete-stack --stack-name opensearch-migration-proxy    --region <REGION>

# Detach and delete the deployer policy
aws iam detach-user-policy \
  --user-name <YOUR_IAM_USER> \
  --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/opensearch-migration-deployer

aws iam delete-policy \
  --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/opensearch-migration-deployer
```
