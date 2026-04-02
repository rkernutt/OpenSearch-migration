import {
  EuiTitle,
  EuiSpacer,
  EuiFormRow,
  EuiFieldText,
  EuiFieldPassword,
  EuiButton,
  EuiCallOut,
  EuiFlexGroup,
  EuiFlexItem,
  EuiPanel,
  EuiText,
  EuiIcon,
  EuiButtonGroup,
  EuiLink,
} from "@elastic/eui";

type TargetType = "cloud" | "self_managed";
type ConnectionStatus = "idle" | "testing" | "ok" | "fail";

interface TargetPageProps {
  targetType: TargetType;
  targetUrl: string;
  targetApiKey: string;
  connectionStatus: ConnectionStatus;
  connectionMsg: string;
  onTargetTypeChange: (v: TargetType) => void;
  onUrlChange: (v: string) => void;
  onApiKeyChange: (v: string) => void;
  onTestConnection: () => void;
  onBack: () => void;
  onNext: () => void;
}

const TARGET_TYPE_OPTIONS = [
  { id: "cloud", label: "Elastic Cloud" },
  { id: "self_managed", label: "Self-Managed" },
];

function esUrlPlaceholder(type: TargetType): string {
  if (type === "cloud") return "https://my-deployment.es.us-east-1.aws.elastic-cloud.com:9243";
  return "http://localhost:9200";
}

export function TargetPage({
  targetType,
  targetUrl,
  targetApiKey,
  connectionStatus,
  connectionMsg,
  onTargetTypeChange,
  onUrlChange,
  onApiKeyChange,
  onTestConnection,
  onBack,
  onNext,
}: TargetPageProps) {
  const canNext = connectionStatus === "ok";

  return (
    <>
      <EuiTitle size="s">
        <h2>Target Cluster</h2>
      </EuiTitle>
      <EuiText color="subdued" size="s">
        <p>Configure your Elastic destination cluster.</p>
      </EuiText>
      <EuiSpacer size="l" />

      <EuiPanel hasBorder paddingSize="l">
        <EuiFormRow label="Deployment Type">
          <EuiButtonGroup
            legend="Target deployment type"
            options={TARGET_TYPE_OPTIONS}
            idSelected={targetType}
            onChange={(id) => onTargetTypeChange(id as TargetType)}
          />
        </EuiFormRow>

        <EuiSpacer size="l" />

        <EuiFormRow
          label="Elasticsearch URL"
          helpText={`e.g. ${esUrlPlaceholder(targetType)}`}
        >
          <EuiFieldText
            value={targetUrl}
            onChange={(e) => onUrlChange(e.target.value)}
            placeholder={esUrlPlaceholder(targetType)}
            prepend={<EuiIcon type="globe" />}
          />
        </EuiFormRow>

        <EuiSpacer size="m" />

        <EuiFormRow
          label="API Key"
          helpText={
            <>
              Base64-encoded Elasticsearch API key.{" "}
              {targetType === "cloud" && (
                <>
                  Create one in{" "}
                  <EuiLink
                    href="https://cloud.elastic.co"
                    target="_blank"
                    external
                  >
                    Elastic Cloud
                  </EuiLink>{" "}
                  → Deployment → Security → API Keys.
                </>
              )}
            </>
          }
        >
          <EuiFieldPassword
            type="dual"
            value={targetApiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
            placeholder="base64-encoded-api-key"
          />
        </EuiFormRow>
      </EuiPanel>

      <EuiSpacer size="l" />

      <EuiFlexGroup gutterSize="m" alignItems="center" responsive={false}>
        <EuiFlexItem grow={false}>
          <EuiButton iconType="arrowLeft" onClick={onBack}>
            Back
          </EuiButton>
        </EuiFlexItem>
        <EuiFlexItem grow={false}>
          <EuiButton
            onClick={onTestConnection}
            isLoading={connectionStatus === "testing"}
            iconType="link"
            isDisabled={!targetUrl || !targetApiKey}
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
            Next: Method
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
