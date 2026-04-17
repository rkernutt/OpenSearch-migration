import {
  EuiTitle,
  EuiSpacer,
  EuiFlexGroup,
  EuiFlexItem,
  EuiCard,
  EuiIcon,
  EuiButton,
  EuiText,
  EuiBadge,
  EuiCallOut,
  EuiPanel,
  EuiSwitch,
} from "@elastic/eui";

export type MigrationMethod = "remote_reindex" | "logstash" | "kafka";

interface MethodPageProps {
  method: MigrationMethod;
  isVpcProxy: boolean;
  onMethodChange: (m: MigrationMethod) => void;
  onVpcProxyChange: (v: boolean) => void;
  onBack: () => void;
  onNext: () => void;
}

interface MethodCard {
  id: MigrationMethod;
  title: string;
  description: string;
  icon: string;
  pros: string[];
  badge?: { label: string; color: "primary" | "accent" | "warning" | "success" };
}

const METHODS: MethodCard[] = [
  {
    id: "remote_reindex",
    title: "Remote Reindex",
    description:
      "Use Elasticsearch's native _reindex API with a remote source pointing to OpenSearch. Elastic pulls data directly over HTTP/HTTPS.",
    icon: "indexOpen",
    pros: [
      "No additional infrastructure",
      "Native Elastic feature",
      "Supports scripted transforms",
    ],
    badge: { label: "Recommended", color: "success" },
  },
  {
    id: "logstash",
    title: "Logstash Pipeline",
    description:
      "Run a Logstash Docker container (or EC2 instance via VPC proxy) as a migration pipeline. Reads from OpenSearch and writes to Elasticsearch.",
    icon: "pipelineApp",
    pros: ["Handles complex transforms", "Resumable with checkpoints", "Enrichment support"],
  },
  {
    id: "kafka",
    title: "Kafka Bridge",
    description:
      "Use Apache Kafka as an intermediate buffer. OpenSearch source connector publishes, Elasticsearch sink connector consumes.",
    icon: "cluster",
    pros: ["Zero data loss guarantee", "Replay capability", "Decoupled from cluster health"],
    badge: { label: "High Reliability", color: "accent" },
  },
];

const VPC_PROXY_CALLOUT: Record<MigrationMethod, string> = {
  remote_reindex:
    "An nginx reverse proxy will be deployed inside your VPC via CloudFormation. The proxy NLB endpoint is used as the reindex target.",
  logstash:
    "Chef will install and configure Logstash directly on an EC2 instance inside your VPC. The migration runs automatically on deploy — no external Logstash container needed.",
  kafka:
    "Chef will install Kafka Connect on an EC2 instance inside your VPC. Source and sink connectors are configured automatically on deploy.",
};

export function MethodPage({
  method,
  isVpcProxy,
  onMethodChange,
  onVpcProxyChange,
  onBack,
  onNext,
}: MethodPageProps) {
  return (
    <>
      <EuiTitle size="s">
        <h2>Migration Method</h2>
      </EuiTitle>
      <EuiText color="subdued" size="s">
        <p>Choose how data will be moved from OpenSearch to Elastic.</p>
      </EuiText>
      <EuiSpacer size="l" />

      {/* Method cards */}
      <EuiFlexGroup gutterSize="m" wrap>
        {METHODS.map((m) => (
          <EuiFlexItem key={m.id} style={{ minWidth: 280, maxWidth: 340 }}>
            <EuiCard
              title={
                <EuiFlexGroup gutterSize="s" alignItems="center" responsive={false}>
                  <EuiFlexItem grow={false}>
                    <span>{m.title}</span>
                  </EuiFlexItem>
                  {m.badge && (
                    <EuiFlexItem grow={false}>
                      <EuiBadge color={m.badge.color}>{m.badge.label}</EuiBadge>
                    </EuiFlexItem>
                  )}
                </EuiFlexGroup>
              }
              icon={
                <EuiIcon type={m.icon} size="xl" color={method === m.id ? "primary" : "subdued"} />
              }
              description={
                <>
                  <EuiText size="s">
                    <p>{m.description}</p>
                  </EuiText>
                  <EuiSpacer size="s" />
                  <EuiText size="xs" color="subdued">
                    <ul style={{ paddingLeft: 16, margin: 0 }}>
                      {m.pros.map((pro) => (
                        <li key={pro}>{pro}</li>
                      ))}
                    </ul>
                  </EuiText>
                </>
              }
              selectable={{
                onClick: () => onMethodChange(m.id),
                isSelected: method === m.id,
              }}
              hasBorder
              paddingSize="l"
            />
          </EuiFlexItem>
        ))}
      </EuiFlexGroup>

      <EuiSpacer size="l" />

      {/* VPC Proxy panel */}
      <EuiPanel hasBorder color="subdued" paddingSize="l">
        <EuiFlexGroup gutterSize="m" alignItems="center" responsive={false}>
          <EuiFlexItem grow={false}>
            <EuiSwitch
              label="Source cluster is in a private VPC (no internet egress)"
              checked={isVpcProxy}
              onChange={(e) => onVpcProxyChange(e.target.checked)}
            />
          </EuiFlexItem>
        </EuiFlexGroup>

        {isVpcProxy && (
          <>
            <EuiSpacer size="m" />
            <EuiCallOut color="primary" size="s" iconType="securityApp">
              <p>{VPC_PROXY_CALLOUT[method]}</p>
            </EuiCallOut>
            <EuiSpacer size="s" />
            <EuiText size="s" color="subdued">
              <p>
                The Deploy Proxy step will appear before index selection for Remote Reindex. For
                Logstash and Kafka, the CloudFormation deploy command is generated on the Execute
                page with all migration parameters pre-filled.
              </p>
            </EuiText>
          </>
        )}
      </EuiPanel>

      <EuiSpacer size="xl" />

      <EuiFlexGroup gutterSize="m" responsive={false}>
        <EuiFlexItem grow={false}>
          <EuiButton iconType="arrowLeft" onClick={onBack}>
            Back
          </EuiButton>
        </EuiFlexItem>
        <EuiFlexItem grow={false}>
          <EuiButton fill iconType="arrowRight" iconSide="right" onClick={onNext}>
            {isVpcProxy && method === "remote_reindex" ? "Next: Deploy Proxy" : "Next: Indices"}
          </EuiButton>
        </EuiFlexItem>
      </EuiFlexGroup>
    </>
  );
}
