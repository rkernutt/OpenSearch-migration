#!/usr/bin/env bash
# =============================================================================
# pre-deploy-ssm-secrets.sh
#
# Creates SSM SecureString parameters BEFORE deploying any migration
# CloudFormation stack.  Storing secrets as SecureString (KMS-encrypted) is
# safer than letting CloudFormation create them as plain String parameters.
#
# Usage:
#   ./pre-deploy-ssm-secrets.sh \
#     --region         us-east-1          \
#     --elastic-url    https://xxx.es.us-east-1.aws.elastic-cloud.com:9243 \
#     --elastic-key    <base64-api-key>   \
#     --os-password    <opensearch-password>   # optional, basic auth only
#
# The CloudFormation templates can then reference these with:
#   {{resolve:ssm-secure:/migration/elastic-api-key}}
#   {{resolve:ssm-secure:/migration/elastic-endpoint}}
#   {{resolve:ssm-secure:/migration/opensearch-password}}
# =============================================================================

set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
ELASTIC_URL=""
ELASTIC_API_KEY=""
OS_PASSWORD=""
KMS_KEY_ID="alias/aws/ssm"   # default AWS-managed SSM KMS key; override with your own CMK

# ── Argument parsing ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)        REGION="$2";         shift 2 ;;
    --elastic-url)   ELASTIC_URL="$2";    shift 2 ;;
    --elastic-key)   ELASTIC_API_KEY="$2"; shift 2 ;;
    --os-password)   OS_PASSWORD="$2";    shift 2 ;;
    --kms-key)       KMS_KEY_ID="$2";     shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$ELASTIC_URL" || -z "$ELASTIC_API_KEY" ]]; then
  echo "ERROR: --elastic-url and --elastic-key are required."
  echo ""
  echo "Usage: $0 --region <region> --elastic-url <url> --elastic-key <api-key> [--os-password <pwd>] [--kms-key <key-id>]"
  exit 1
fi

echo "=== Migration SSM SecureString Setup ==="
echo "Region:      $REGION"
echo "KMS Key:     $KMS_KEY_ID"
echo "Elastic URL: $ELASTIC_URL"
echo ""

put_secure() {
  local name="$1"
  local value="$2"
  local description="$3"

  echo -n "  Storing $name ... "
  aws ssm put-parameter \
    --region "$REGION" \
    --name "$name" \
    --value "$value" \
    --type "SecureString" \
    --key-id "$KMS_KEY_ID" \
    --description "$description" \
    --overwrite \
    --tier Standard \
    --tags \
      "Key=Project,Value=opensearch-migration" \
      "Key=ManagedBy,Value=pre-deploy-ssm-secrets.sh" \
    > /dev/null
  echo "OK"
}

put_secure \
  "/migration/elastic-endpoint" \
  "$ELASTIC_URL" \
  "Elastic Cloud / Elasticsearch endpoint URL for migration"

put_secure \
  "/migration/elastic-api-key" \
  "$ELASTIC_API_KEY" \
  "Elasticsearch API key (base64) for migration"

put_secure \
  "/migration/kafka/elastic-api-key" \
  "$ELASTIC_API_KEY" \
  "Elasticsearch API key (base64) for Kafka migration stack"

if [[ -n "$OS_PASSWORD" ]]; then
  put_secure \
    "/migration/opensearch-password" \
    "$OS_PASSWORD" \
    "OpenSearch basic-auth password for migration"
fi

echo ""
echo "=== Parameters created ==="
aws ssm get-parameters-by-path \
  --region "$REGION" \
  --path "/migration" \
  --recursive \
  --with-decryption \
  --query "Parameters[*].{Name:Name,Type:Type}" \
  --output table

echo ""
echo "=== Next steps ==="
echo "Deploy the CloudFormation stack — the templates will reference these"
echo "SecureString parameters automatically via:"
echo "  {{resolve:ssm-secure:/migration/elastic-api-key}}"
echo "  {{resolve:ssm-secure:/migration/elastic-endpoint}}"
echo ""
echo "To clean up all parameters after migration:"
echo "  aws ssm delete-parameters --region $REGION \\"
echo "    --names /migration/elastic-endpoint \\"
echo "            /migration/elastic-api-key \\"
echo "            /migration/kafka/elastic-api-key \\"
echo "            /migration/opensearch-password"
