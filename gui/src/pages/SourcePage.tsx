import {
  EuiTitle,
  EuiSpacer,
  EuiFormRow,
  EuiFieldText,
  EuiFieldPassword,
  EuiButtonGroup,
  EuiButton,
  EuiCallOut,
  EuiFlexGroup,
  EuiFlexItem,
  EuiPanel,
  EuiText,
  EuiIcon,
  EuiSelect,
  EuiHorizontalRule,
} from "@elastic/eui";

type AuthType = "iam" | "basic";
type ConnectionStatus = "idle" | "testing" | "ok" | "fail";

interface SourcePageProps {
  sourceEndpoint: string;
  sourceRegion: string;
  sourceAuthType: AuthType;
  sourceUsername: string;
  sourcePassword: string;
  connectionStatus: ConnectionStatus;
  connectionMsg: string;
  onEndpointChange: (v: string) => void;
  onRegionChange: (v: string) => void;
  onAuthTypeChange: (v: AuthType) => void;
  onUsernameChange: (v: string) => void;
  onPasswordChange: (v: string) => void;
  onTestConnection: () => void;
  onNext: () => void;
}

const AUTH_OPTIONS = [
  { id: "iam", label: "AWS IAM / SigV4" },
  { id: "basic", label: "Username / Password" },
];

const AWS_REGIONS = [
  "us-east-1",
  "us-east-2",
  "us-west-1",
  "us-west-2",
  "eu-west-1",
  "eu-west-2",
  "eu-west-3",
  "eu-central-1",
  "ap-southeast-1",
  "ap-southeast-2",
  "ap-northeast-1",
  "ap-northeast-2",
  "ap-south-1",
  "sa-east-1",
  "ca-central-1",
].map((r) => ({ value: r, text: r }));

export function SourcePage({
  sourceEndpoint,
  sourceRegion,
  sourceAuthType,
  sourceUsername,
  sourcePassword,
  connectionStatus,
  connectionMsg,
  onEndpointChange,
  onRegionChange,
  onAuthTypeChange,
  onUsernameChange,
  onPasswordChange,
  onTestConnection,
  onNext,
}: SourcePageProps) {
  const canNext = connectionStatus === "ok";

  return (
    <>
      <EuiTitle size="s">
        <h2>Source Cluster</h2>
      </EuiTitle>
      <EuiText color="subdued" size="s">
        <p>Configure your Amazon OpenSearch Service source cluster.</p>
      </EuiText>
      <EuiSpacer size="l" />

      {/* Info callout for VPC mode */}
      <EuiCallOut
        title="Amazon OpenSearch Service in VPC mode?"
        color="primary"
        iconType="iInCircle"
        size="s"
      >
        <p>
          If your cluster is in a VPC with no internet access, you will need to deploy the{" "}
          <strong>VPC Proxy</strong> option in Step 3, or run this tool from within the same VPC.
        </p>
      </EuiCallOut>
      <EuiSpacer size="l" />

      <EuiPanel hasBorder paddingSize="l">
        <EuiFormRow
          label="OpenSearch Endpoint URL"
          helpText="e.g. https://search-my-domain-abc123.us-east-1.es.amazonaws.com"
        >
          <EuiFieldText
            value={sourceEndpoint}
            onChange={(e) => onEndpointChange(e.target.value)}
            placeholder="https://search-my-domain-abc123.us-east-1.es.amazonaws.com"
            prepend={<EuiIcon type="globe" />}
          />
        </EuiFormRow>

        <EuiSpacer size="m" />

        <EuiFormRow label="AWS Region">
          <EuiSelect
            options={AWS_REGIONS}
            value={sourceRegion}
            onChange={(e) => onRegionChange(e.target.value)}
          />
        </EuiFormRow>

        <EuiSpacer size="l" />
        <EuiHorizontalRule margin="none" />
        <EuiSpacer size="l" />

        <EuiFormRow
          label="Authentication"
          helpText={
            sourceAuthType === "iam"
              ? "SigV4 signing will use the IAM credentials configured in your migration environment (EC2 role, ECS task role, or AWS_* env vars)."
              : "Fine-grained access control credentials for the OpenSearch domain."
          }
        >
          <EuiButtonGroup
            legend="Authentication type"
            options={AUTH_OPTIONS}
            idSelected={sourceAuthType}
            onChange={(id) => onAuthTypeChange(id as AuthType)}
          />
        </EuiFormRow>

        {sourceAuthType === "basic" && (
          <>
            <EuiSpacer size="m" />
            <EuiFlexGroup gutterSize="m">
              <EuiFlexItem>
                <EuiFormRow label="Username">
                  <EuiFieldText
                    value={sourceUsername}
                    onChange={(e) => onUsernameChange(e.target.value)}
                    placeholder="admin"
                  />
                </EuiFormRow>
              </EuiFlexItem>
              <EuiFlexItem>
                <EuiFormRow label="Password">
                  <EuiFieldPassword
                    type="dual"
                    value={sourcePassword}
                    onChange={(e) => onPasswordChange(e.target.value)}
                  />
                </EuiFormRow>
              </EuiFlexItem>
            </EuiFlexGroup>
          </>
        )}
      </EuiPanel>

      <EuiSpacer size="l" />

      <EuiFlexGroup gutterSize="m" alignItems="center" responsive={false}>
        <EuiFlexItem grow={false}>
          <EuiButton
            onClick={onTestConnection}
            isLoading={connectionStatus === "testing"}
            iconType="link"
            isDisabled={!sourceEndpoint}
          >
            Test Connection
          </EuiButton>
        </EuiFlexItem>
        <EuiFlexItem grow={false}>
          <EuiButton
            fill
            onClick={onNext}
            iconType="arrowRight"
            iconSide="right"
            isDisabled={!canNext}
          >
            Next: Target
          </EuiButton>
        </EuiFlexItem>
      </EuiFlexGroup>

      {connectionStatus === "ok" && (
        <>
          <EuiSpacer size="m" />
          <EuiCallOut title="Connection successful" color="success" iconType="check" size="s">
            <p>{connectionMsg}</p>
          </EuiCallOut>
        </>
      )}
      {connectionStatus === "fail" && (
        <>
          <EuiSpacer size="m" />
          <EuiCallOut title="Connection failed" color="danger" iconType="cross" size="s">
            <p>{connectionMsg}</p>
          </EuiCallOut>
        </>
      )}
    </>
  );
}
