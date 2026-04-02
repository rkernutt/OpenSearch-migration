import { useState } from "react";
export type ProxyStatus = "idle" | "deploying" | "deployed" | "testing" | "confirmed" | "failed";

import {
  EuiTitle,
  EuiSpacer,
  EuiFormRow,
  EuiFieldText,
  EuiButton,
  EuiButtonEmpty,
  EuiCallOut,
  EuiFlexGroup,
  EuiFlexItem,
  EuiPanel,
  EuiText,
  EuiSteps,
  EuiCodeBlock,
  EuiHorizontalRule,
  EuiSelect,
  EuiFieldPassword,
  EuiBadge,
  EuiIcon,
  EuiCopy,
} from "@elastic/eui";

interface ProxyDeployPageProps {
  // Pre-populated from previous steps
  sourceRegion: string;
  targetUrl: string;
  // Proxy deploy config
  vpcId: string;
  subnetId: string;
  allowedCidr: string;
  instanceType: string;
  keyPairName: string;
  proxyStatus: ProxyStatus;
  proxyEndpoint: string;
  proxyTestMsg: string;
  // Handlers
  onVpcIdChange: (v: string) => void;
  onSubnetIdChange: (v: string) => void;
  onAllowedCidrChange: (v: string) => void;
  onInstanceTypeChange: (v: string) => void;
  onKeyPairNameChange: (v: string) => void;
  onProxyStatusChange: (s: ProxyStatus) => void;
  onProxyEndpointChange: (v: string) => void;
  onProxyTestMsgChange: (v: string) => void;
  onTestProxy: () => void;
  onBack: () => void;
  onNext: () => void;
}

const INSTANCE_OPTIONS = [
  { value: "t3.micro", text: "t3.micro (1 vCPU, 1 GB)" },
  { value: "t3.small", text: "t3.small (2 vCPU, 2 GB) — recommended" },
  { value: "t3.medium", text: "t3.medium (2 vCPU, 4 GB)" },
  { value: "m5.large", text: "m5.large (2 vCPU, 8 GB)" },
];

export function ProxyDeployPage({
  sourceRegion,
  targetUrl,
  vpcId,
  subnetId,
  allowedCidr,
  instanceType,
  keyPairName,
  proxyStatus,
  proxyEndpoint,
  proxyTestMsg,
  onVpcIdChange,
  onSubnetIdChange,
  onAllowedCidrChange,
  onInstanceTypeChange,
  onKeyPairNameChange,
  onProxyStatusChange,
  onProxyEndpointChange,
  onProxyTestMsgChange,
  onTestProxy,
  onBack,
  onNext,
}: ProxyDeployPageProps) {
  const [showAdvanced, setShowAdvanced] = useState(false);

  const cfnDeployCmd = `aws cloudformation deploy \\
  --template-file iac/cloudformation/vpc-proxy.yaml \\
  --stack-name opensearch-migration-proxy \\
  --parameter-overrides \\
    VpcId=${vpcId || "<YOUR_VPC_ID>"} \\
    SubnetId=${subnetId || "<PRIVATE_SUBNET_ID>"} \\
    ElasticCloudEndpoint=${targetUrl || "<ELASTIC_CLOUD_URL>"} \\
    AllowedCidr=${allowedCidr || "10.0.0.0/8"} \\
    InstanceType=${instanceType || "t3.small"} \\${keyPairName ? `\n    KeyPairName=${keyPairName} \\` : ""}
  --capabilities CAPABILITY_IAM \\
  --region ${sourceRegion}`;

  const cfnOutputCmd = `# Get the proxy endpoint from the stack outputs
aws cloudformation describe-stacks \\
  --stack-name opensearch-migration-proxy \\
  --query "Stacks[0].Outputs[?OutputKey=='ProxyEndpoint'].OutputValue" \\
  --output text \\
  --region ${sourceRegion}`;

  const canNext = proxyStatus === "confirmed" && !!proxyEndpoint;

  const deploySteps = [
    {
      title: "Configure VPC parameters",
      children: (
        <>
          <EuiPanel hasBorder paddingSize="l">
            <EuiFlexGroup gutterSize="l" wrap>
              <EuiFlexItem style={{ minWidth: 260 }}>
                <EuiFormRow
                  label="VPC ID"
                  helpText="VPC where OpenSearch is deployed"
                  isInvalid={!vpcId}
                  error={!vpcId ? "Required" : undefined}
                >
                  <EuiFieldText
                    value={vpcId}
                    onChange={(e) => onVpcIdChange(e.target.value)}
                    placeholder="vpc-0abc123def456"
                    prepend="vpc-"
                  />
                </EuiFormRow>
              </EuiFlexItem>
              <EuiFlexItem style={{ minWidth: 260 }}>
                <EuiFormRow
                  label="Private Subnet ID"
                  helpText="Subnet with NAT gateway for outbound internet"
                  isInvalid={!subnetId}
                  error={!subnetId ? "Required" : undefined}
                >
                  <EuiFieldText
                    value={subnetId}
                    onChange={(e) => onSubnetIdChange(e.target.value)}
                    placeholder="subnet-0abc123def456"
                  />
                </EuiFormRow>
              </EuiFlexItem>
            </EuiFlexGroup>

            <EuiSpacer size="m" />
            <EuiFlexGroup gutterSize="l" wrap>
              <EuiFlexItem style={{ minWidth: 260 }}>
                <EuiFormRow
                  label="Allowed CIDR"
                  helpText="Which CIDR can reach the proxy on port 9200"
                >
                  <EuiFieldText
                    value={allowedCidr}
                    onChange={(e) => onAllowedCidrChange(e.target.value)}
                    placeholder="10.0.0.0/8"
                  />
                </EuiFormRow>
              </EuiFlexItem>
              <EuiFlexItem style={{ minWidth: 260 }}>
                <EuiFormRow label="Instance Type">
                  <EuiSelect
                    options={INSTANCE_OPTIONS}
                    value={instanceType}
                    onChange={(e) => onInstanceTypeChange(e.target.value)}
                  />
                </EuiFormRow>
              </EuiFlexItem>
            </EuiFlexGroup>

            {showAdvanced && (
              <>
                <EuiSpacer size="m" />
                <EuiFormRow
                  label="Key Pair Name"
                  helpText="Optional — leave blank to use SSM Session Manager instead"
                >
                  <EuiFieldText
                    value={keyPairName}
                    onChange={(e) => onKeyPairNameChange(e.target.value)}
                    placeholder="my-keypair (optional)"
                  />
                </EuiFormRow>
              </>
            )}

            <EuiSpacer size="s" />
            <EuiButtonEmpty
              size="xs"
              iconType={showAdvanced ? "arrowUp" : "arrowDown"}
              onClick={() => setShowAdvanced((v) => !v)}
            >
              {showAdvanced ? "Hide" : "Show"} advanced options
            </EuiButtonEmpty>
          </EuiPanel>
        </>
      ),
      status: vpcId && subnetId ? ("complete" as const) : ("incomplete" as const),
    },
    {
      title: "Deploy the CloudFormation stack",
      children: (
        <>
          <EuiText size="s" color="subdued">
            <p>
              Run this command from your workstation or CI/CD environment. Requires AWS CLI
              credentials with CloudFormation, EC2, IAM, and ELB permissions in{" "}
              <strong>{sourceRegion}</strong>.
            </p>
          </EuiText>
          <EuiSpacer size="m" />
          <EuiCodeBlock language="bash" fontSize="s" paddingSize="m" isCopyable overflowHeight={180}>
            {cfnDeployCmd}
          </EuiCodeBlock>
          <EuiSpacer size="m" />
          <EuiText size="s" color="subdued">
            <p>Deployment takes ~3–5 minutes. Once complete, retrieve the proxy endpoint:</p>
          </EuiText>
          <EuiSpacer size="s" />
          <EuiCodeBlock language="bash" fontSize="s" paddingSize="m" isCopyable>
            {cfnOutputCmd}
          </EuiCodeBlock>
          <EuiSpacer size="m" />
          <EuiCallOut
            title="What gets deployed?"
            color="primary"
            iconType="iInCircle"
            size="s"
          >
            <EuiText size="xs">
              <ul style={{ paddingLeft: 16, margin: 0 }}>
                <li>EC2 Auto Scaling Group (1 instance, Amazon Linux 2023, IMDSv2)</li>
                <li>nginx reverse proxy — port 9200 → Elastic Cloud HTTPS endpoint</li>
                <li>Internal Network Load Balancer on port 9200</li>
                <li>IAM instance profile with SSM access (no SSH required)</li>
                <li>CloudWatch agent for metrics and nginx logs</li>
              </ul>
            </EuiText>
          </EuiCallOut>
        </>
      ),
      status: proxyStatus === "deployed" || proxyStatus === "confirmed" || proxyStatus === "testing"
        ? ("complete" as const)
        : ("incomplete" as const),
    },
    {
      title: "Enter proxy endpoint and verify connectivity",
      children: (
        <>
          <EuiText size="s" color="subdued">
            <p>
              Paste the NLB DNS name from the stack output. This is the internal endpoint your
              migration tool will use instead of the Elastic Cloud URL.
            </p>
          </EuiText>
          <EuiSpacer size="m" />
          <EuiPanel hasBorder paddingSize="l">
            <EuiFormRow
              label="Proxy Endpoint"
              helpText="e.g. http://internal-opensearch-migration-proxy-abc123.us-east-1.elb.amazonaws.com:9200"
            >
              <EuiFieldText
                value={proxyEndpoint}
                onChange={(e) => {
                  onProxyEndpointChange(e.target.value);
                  if (proxyStatus === "confirmed") onProxyStatusChange("deployed");
                }}
                placeholder="http://internal-nlb-dns.region.elb.amazonaws.com:9200"
                prepend={<EuiIcon type="link" />}
              />
            </EuiFormRow>

            <EuiSpacer size="m" />

            <EuiFlexGroup gutterSize="m" alignItems="center" responsive={false}>
              <EuiFlexItem grow={false}>
                <EuiButton
                  iconType="link"
                  onClick={() => {
                    onProxyStatusChange("testing");
                    onTestProxy();
                  }}
                  isLoading={proxyStatus === "testing"}
                  isDisabled={!proxyEndpoint || proxyStatus === "testing"}
                >
                  Test Proxy Connectivity
                </EuiButton>
              </EuiFlexItem>
              {proxyStatus === "deployed" && proxyEndpoint && (
                <EuiFlexItem grow={false}>
                  <EuiButtonEmpty
                    iconType="check"
                    onClick={() => {
                      onProxyStatusChange("confirmed");
                      onProxyTestMsgChange("Manually confirmed — skipped connectivity test.");
                    }}
                    size="s"
                  >
                    Skip test (mark confirmed)
                  </EuiButtonEmpty>
                </EuiFlexItem>
              )}
            </EuiFlexGroup>

            {proxyStatus === "confirmed" && (
              <>
                <EuiSpacer size="m" />
                <EuiCallOut title="Proxy verified" color="success" iconType="check" size="s">
                  <p>{proxyTestMsg}</p>
                </EuiCallOut>
              </>
            )}
            {proxyStatus === "failed" && (
              <>
                <EuiSpacer size="m" />
                <EuiCallOut title="Proxy unreachable" color="danger" iconType="cross" size="s">
                  <EuiText size="s">
                    <p>{proxyTestMsg}</p>
                    <p>
                      Ensure the proxy EC2 instance is running, the NLB target is healthy, and
                      this machine is in the allowed CIDR range ({allowedCidr || "10.0.0.0/8"}).
                    </p>
                  </EuiText>
                </EuiCallOut>
              </>
            )}
          </EuiPanel>
        </>
      ),
      status: proxyStatus === "confirmed"
        ? ("complete" as const)
        : ("incomplete" as const),
    },
  ];

  return (
    <>
      <EuiFlexGroup gutterSize="m" alignItems="center" responsive={false}>
        <EuiFlexItem grow={false}>
          <EuiTitle size="s">
            <h2>Deploy VPC Proxy</h2>
          </EuiTitle>
        </EuiFlexItem>
        <EuiFlexItem grow={false}>
          <EuiBadge color="warning">Phase 2</EuiBadge>
        </EuiFlexItem>
      </EuiFlexGroup>

      <EuiText color="subdued" size="s">
        <p>
          Deploy an nginx reverse proxy inside your VPC to bridge the private OpenSearch cluster
          to Elastic Cloud. This enables migration from environments with no direct internet
          egress.
        </p>
      </EuiText>
      <EuiSpacer size="l" />

      <EuiCallOut
        title="Architecture overview"
        color="primary"
        iconType="securityApp"
        size="s"
      >
        <EuiText size="s">
          <p>
            <strong>Migration tool</strong> (inside VPC)
            {" → "}
            <code>http://proxy:9200</code>
            {" → "}
            <strong>nginx proxy EC2</strong> (VPC, with NAT)
            {" → "}
            <code>{targetUrl || "https://elastic-cloud.com:9243"}</code>
          </p>
        </EuiText>
      </EuiCallOut>
      <EuiSpacer size="l" />

      <EuiSteps steps={deploySteps} />

      <EuiSpacer size="l" />
      <EuiHorizontalRule />
      <EuiSpacer size="m" />

      <EuiFlexGroup gutterSize="m" responsive={false}>
        <EuiFlexItem grow={false}>
          <EuiButton iconType="arrowLeft" onClick={onBack}>
            Back
          </EuiButton>
        </EuiFlexItem>
        <EuiFlexItem grow={false}>
          <EuiButton
            fill
            iconType="arrowRight"
            iconSide="right"
            onClick={onNext}
            isDisabled={!canNext}
          >
            Next: Indices
          </EuiButton>
        </EuiFlexItem>
        {!canNext && (
          <EuiFlexItem>
            <EuiText size="xs" color="subdued" style={{ paddingTop: 8 }}>
              <EuiIcon type="iInCircle" size="s" />{" "}
              {!proxyEndpoint
                ? "Enter the proxy endpoint and test connectivity to continue."
                : "Test or confirm proxy connectivity to continue."}
            </EuiText>
          </EuiFlexItem>
        )}
      </EuiFlexGroup>
    </>
  );
}
