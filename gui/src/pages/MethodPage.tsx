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
} from "@elastic/eui";

export type MigrationMethod = "remote_reindex" | "logstash" | "kafka" | "vpc_proxy";

interface MethodPageProps {
  method: MigrationMethod;
  onMethodChange: (m: MigrationMethod) => void;
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
  phase2?: boolean;
}

const METHODS: MethodCard[] = [
  {
    id: "remote_reindex",
    title: "Remote Reindex",
    description:
      "Use Elasticsearch's native _reindex API with a remote source pointing to OpenSearch. Elastic pulls data directly over HTTP.",
    icon: "indexOpen",
    pros: ["No additional infrastructure", "Native Elastic feature", "Supports scripted transforms"],
    badge: { label: "Recommended", color: "success" },
  },
  {
    id: "logstash",
    title: "Logstash Pipeline",
    description:
      "Run a Logstash Docker container as a migration pipeline. Reads from OpenSearch via HTTP input and writes to Elasticsearch output.",
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
  {
    id: "vpc_proxy",
    title: "VPC Proxy + Reindex",
    description:
      "Deploy a CloudFormation-managed proxy inside your VPC to bridge private OpenSearch to Elastic Cloud, then run Remote Reindex through it.",
    icon: "securityApp",
    pros: ["Works in fully private VPCs", "No internet exposure of source", "CloudFormation managed"],
    badge: { label: "Phase 2", color: "warning" },
    phase2: true,
  },
];

export function MethodPage({ method, onMethodChange, onBack, onNext }: MethodPageProps) {
  return (
    <>
      <EuiTitle size="s">
        <h2>Migration Method</h2>
      </EuiTitle>
      <EuiText color="subdued" size="s">
        <p>Choose how data will be moved from OpenSearch to Elastic.</p>
      </EuiText>
      <EuiSpacer size="l" />

      {method === "vpc_proxy" && (
        <>
          <EuiCallOut
            title="VPC Proxy selected — Phase 2 feature"
            color="warning"
            iconType="warning"
            size="s"
          >
            <p>
              The VPC Proxy option will guide you through deploying a CloudFormation stack before
              the migration begins. You can configure and deploy it in the Execute step.
            </p>
          </EuiCallOut>
          <EuiSpacer size="l" />
        </>
      )}

      <EuiFlexGroup gutterSize="m" wrap>
        {METHODS.map((m) => (
          <EuiFlexItem key={m.id} style={{ minWidth: 280, maxWidth: 320 }}>
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
              icon={<EuiIcon type={m.icon} size="xl" color={method === m.id ? "primary" : "subdued"} />}
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

      <EuiSpacer size="xl" />

      <EuiFlexGroup gutterSize="m" responsive={false}>
        <EuiFlexItem grow={false}>
          <EuiButton iconType="arrowLeft" onClick={onBack}>
            Back
          </EuiButton>
        </EuiFlexItem>
        <EuiFlexItem grow={false}>
          <EuiButton fill iconType="arrowRight" iconSide="right" onClick={onNext}>
            Next: Indices
          </EuiButton>
        </EuiFlexItem>
      </EuiFlexGroup>
    </>
  );
}
