import {
  EuiTitle,
  EuiSpacer,
  EuiButton,
  EuiFlexGroup,
  EuiFlexItem,
  EuiText,
  EuiCallOut,
  EuiPanel,
  EuiCodeBlock,
  EuiTabbedContent,
  EuiAccordion,
  EuiBadge,
  EuiHorizontalRule,
  EuiIcon,
} from "@elastic/eui";
import type { MigrationMethod } from "./MethodPage";
import type { TargetType } from "./TargetPage";

interface ExecutePageProps {
  sourceEndpoint: string;
  sourceRegion: string;
  sourceAuthType: "iam" | "basic";
  sourceUsername: string;
  sourcePassword: string;
  targetUrl: string;
  targetApiKey: string;
  targetType: TargetType;
  migrationMethod: MigrationMethod;
  isVpcProxy: boolean;
  proxyEndpoint: string;
  selectedIndices: string[];
  availableIndices?: Array<{ name: string; docCount: number; sizeBytes: number }>;
  batchSize: number;
  slices: string;
  // VPC proxy / CFN params
  vpcId: string;
  subnetId: string;
  allowedCidr: string;
  instanceType: string;
  onBack: () => void;
}

// ── Config generators ─────────────────────────────────────────────────────────

function genRemoteReindexDevTools(
  sourceEndpoint: string,
  indices: string[],
  batchSize: number,
  slices: string,
): string {
  return indices
    .map((idx) => {
      const slicesVal = slices === "auto" ? '"auto"' : Number(slices);
      return `POST _reindex?slices=${slicesVal}&wait_for_completion=false
{
  "source": {
    "remote": {
      "host": "${sourceEndpoint}",
      "username": "admin",
      "password": "<OPENSEARCH_PASSWORD>"
    },
    "index": "${idx}",
    "size": ${batchSize}
  },
  "dest": {
    "index": "${idx}"
  }
}`;
    })
    .join("\n\n");
}

function genRemoteReindexCurl(
  sourceEndpoint: string,
  effectiveTargetUrl: string,
  targetApiKey: string,
  indices: string[],
  batchSize: number,
  slices: string,
): string {
  return indices
    .map((idx) => {
      const slicesVal = slices === "auto" ? "auto" : slices;
      return `curl -X POST "${effectiveTargetUrl}/_reindex?slices=${slicesVal}&wait_for_completion=false" \\
  -H "Authorization: ApiKey ${targetApiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "source": {
      "remote": {
        "host": "${sourceEndpoint}",
        "username": "admin",
        "password": "<OPENSEARCH_PASSWORD>"
      },
      "index": "${idx}",
      "size": ${batchSize}
    },
    "dest": { "index": "${idx}" }
  }'`;
    })
    .join("\n\n");
}

function genLogstashDockerCompose(
  sourceEndpoint: string,
  sourceAuthType: "iam" | "basic",
  sourceUsername: string,
  sourcePassword: string,
  targetUrl: string,
  targetApiKey: string,
  indices: string[],
): string {
  return `version: "3.8"
services:
  logstash:
    image: docker.elastic.co/logstash/logstash:8.17.0
    environment:
      - OPENSEARCH_ENDPOINT=${sourceEndpoint}
      - OPENSEARCH_USERNAME=${sourceAuthType === "basic" ? sourceUsername : "admin"}
      - OPENSEARCH_PASSWORD=${sourceAuthType === "basic" ? sourcePassword : "<YOUR_PASSWORD>"}
      - ELASTIC_URL=${targetUrl}
      - ELASTIC_API_KEY=${targetApiKey}
      - INDICES_TO_MIGRATE=${indices.join(",")}
    volumes:
      - ./pipeline:/usr/share/logstash/pipeline
    ports:
      - "5044:5044"
    restart: unless-stopped`;
}

function genLogstashConf(sourceEndpoint: string, indices: string[], batchSize: number): string {
  const indexList = indices.map((i) => `"${i}"`).join(", ");
  return `input {
  elasticsearch {
    hosts => ["${sourceEndpoint}"]
    user => "\${OPENSEARCH_USERNAME}"
    password => "\${OPENSEARCH_PASSWORD}"
    index => [${indexList}]
    query => '{ "query": { "match_all": {} } }'
    size => ${batchSize}
    scroll => "5m"
    docinfo => true
    docinfo_target => "[@metadata][doc]"
  }
}

filter {
  mutate {
    add_field => {
      "[@metadata][_index]" => "%{[@metadata][doc][_index]}"
      "[@metadata][_id]" => "%{[@metadata][doc][_id]}"
    }
    remove_field => ["@version", "@timestamp"]
  }
}

output {
  elasticsearch {
    hosts => ["\${ELASTIC_URL}"]
    api_key => "\${ELASTIC_API_KEY}"
    index => "%{[@metadata][_index]}"
    document_id => "%{[@metadata][_id]}"
    action => "index"
  }
}`;
}

function genKafkaSourceConnector(sourceEndpoint: string, indices: string[]): string {
  return `{
  "name": "opensearch-source-connector",
  "config": {
    "connector.class": "com.github.dariobalinzo.ElasticSourceConnector",
    "tasks.max": "1",
    "es.host": "${sourceEndpoint}",
    "es.user": "admin",
    "es.password": "<OPENSEARCH_PASSWORD>",
    "index.prefix": "${indices.join(",")}",
    "topic.prefix": "migration.",
    "incrementing.field.name": "@timestamp",
    "poll.interval.ms": "1000"
  }
}`;
}

function genKafkaSinkConnector(targetUrl: string, targetApiKey: string, indices: string[]): string {
  return `{
  "name": "elasticsearch-sink-connector",
  "config": {
    "connector.class": "io.confluent.connect.elasticsearch.ElasticsearchSinkConnector",
    "tasks.max": "1",
    "topics": "${indices.map((i) => `migration.${i}`).join(",")}",
    "connection.url": "${targetUrl}",
    "connection.username": "elastic",
    "connection.password": "${targetApiKey}",
    "type.name": "_doc",
    "key.ignore": "true",
    "schema.ignore": "true",
    "drop.invalid.message": "false"
  }
}`;
}

function genLogstashVpcCfn(props: {
  vpcId: string;
  subnetId: string;
  sourceEndpoint: string;
  sourceAuthType: "iam" | "basic";
  sourceUsername: string;
  targetUrl: string;
  targetApiKey: string;
  indices: string[];
  batchSize: number;
  instanceType: string;
  allowedCidr: string;
  sourceRegion: string;
}): string {
  const {
    vpcId,
    subnetId,
    sourceEndpoint,
    sourceAuthType,
    sourceUsername,
    targetUrl,
    targetApiKey,
    indices,
    batchSize,
    instanceType,
    allowedCidr,
    sourceRegion,
  } = props;
  return `aws cloudformation deploy \\
  --template-file iac/cloudformation/vpc-logstash.yaml \\
  --stack-name opensearch-migration-logstash \\
  --parameter-overrides \\
    VpcId=${vpcId || "<VPC_ID>"} \\
    SubnetId=${subnetId || "<SUBNET_ID>"} \\
    OpenSearchEndpoint=${sourceEndpoint} \\
    OpenSearchAuthType=${sourceAuthType} \\
    OpenSearchUsername=${sourceAuthType === "basic" ? sourceUsername : ""} \\
    ElasticEndpoint=${targetUrl} \\
    ElasticApiKeyParam=${targetApiKey} \\
    IndicesToMigrate=${indices.join(",")} \\
    BatchSize=${batchSize} \\
    InstanceType=${instanceType || "t3.medium"} \\
    AllowedCidr=${allowedCidr || "10.0.0.0/8"} \\
  --capabilities CAPABILITY_IAM \\
  --region ${sourceRegion}`;
}

function genKafkaVpcCfn(props: {
  vpcId: string;
  subnetId: string;
  sourceEndpoint: string;
  targetUrl: string;
  targetApiKey: string;
  indices: string[];
  instanceType: string;
  allowedCidr: string;
  sourceRegion: string;
}): string {
  const {
    vpcId,
    subnetId,
    sourceEndpoint,
    targetUrl,
    targetApiKey,
    indices,
    instanceType,
    allowedCidr,
    sourceRegion,
  } = props;
  return `aws cloudformation deploy \\
  --template-file iac/cloudformation/vpc-kafka.yaml \\
  --stack-name opensearch-migration-kafka \\
  --parameter-overrides \\
    VpcId=${vpcId || "<VPC_ID>"} \\
    SubnetId=${subnetId || "<SUBNET_ID>"} \\
    OpenSearchEndpoint=${sourceEndpoint} \\
    ElasticEndpoint=${targetUrl} \\
    ElasticApiKeyParam=${targetApiKey} \\
    IndicesToMigrate=${indices.join(",")} \\
    InstanceType=${instanceType || "m5.large"} \\
    AllowedCidr=${allowedCidr || "10.0.0.0/8"} \\
  --capabilities CAPABILITY_IAM \\
  --region ${sourceRegion}`;
}

// ── Target type badge ─────────────────────────────────────────────────────────

function targetTypeBadge(t: TargetType) {
  if (t === "cloud_hosted") return <EuiBadge color="primary">Cloud Hosted</EuiBadge>;
  if (t === "cloud_serverless") return <EuiBadge color="accent">Serverless</EuiBadge>;
  return <EuiBadge color="hollow">Self-Managed</EuiBadge>;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function ExecutePage({
  sourceEndpoint,
  sourceRegion,
  sourceAuthType,
  sourceUsername,
  sourcePassword,
  targetUrl,
  targetApiKey,
  targetType,
  migrationMethod,
  isVpcProxy,
  proxyEndpoint,
  selectedIndices,
  batchSize,
  slices,
  vpcId,
  subnetId,
  allowedCidr,
  instanceType,
  onBack,
}: ExecutePageProps) {
  // For remote_reindex + isVpcProxy: route commands through the proxy NLB endpoint
  const effectiveTargetUrl =
    isVpcProxy && migrationMethod === "remote_reindex" && proxyEndpoint ? proxyEndpoint : targetUrl;

  const methodLabel: Record<MigrationMethod, string> = {
    remote_reindex: "Remote Reindex",
    logstash: "Logstash Pipeline",
    kafka: "Kafka Bridge",
  };

  // ── Remote Reindex tabs ──────────────────────────────────────────────────
  const remoteReindexTabs = [
    {
      id: "devtools",
      name: "Dev Tools",
      content: (
        <>
          <EuiSpacer size="m" />
          <EuiText size="s" color="subdued">
            <p>
              Paste this into <strong>Kibana &rarr; Dev Tools</strong> on your Elastic target
              cluster. Each POST starts an async reindex task; monitor with{" "}
              <code>GET _tasks?actions=*reindex&amp;detailed</code>.
            </p>
          </EuiText>
          <EuiSpacer size="m" />
          <EuiCodeBlock
            language="json"
            fontSize="s"
            paddingSize="m"
            isCopyable
            overflowHeight={400}
          >
            {genRemoteReindexDevTools(sourceEndpoint, selectedIndices, batchSize, slices)}
          </EuiCodeBlock>
        </>
      ),
    },
    {
      id: "curl",
      name: "cURL",
      content: (
        <>
          <EuiSpacer size="m" />
          <EuiText size="s" color="subdued">
            <p>Run these commands from any machine with network access to both clusters.</p>
          </EuiText>
          <EuiSpacer size="m" />
          <EuiCodeBlock
            language="bash"
            fontSize="s"
            paddingSize="m"
            isCopyable
            overflowHeight={400}
          >
            {genRemoteReindexCurl(
              sourceEndpoint,
              effectiveTargetUrl,
              targetApiKey,
              selectedIndices,
              batchSize,
              slices,
            )}
          </EuiCodeBlock>
        </>
      ),
    },
  ];

  // ── Logstash tabs (no VPC) ───────────────────────────────────────────────
  const logstashTabs = [
    {
      id: "compose",
      name: "docker-compose.yml",
      content: (
        <>
          <EuiSpacer size="m" />
          <EuiCodeBlock
            language="yaml"
            fontSize="s"
            paddingSize="m"
            isCopyable
            overflowHeight={300}
          >
            {genLogstashDockerCompose(
              sourceEndpoint,
              sourceAuthType,
              sourceUsername,
              sourcePassword,
              targetUrl,
              targetApiKey,
              selectedIndices,
            )}
          </EuiCodeBlock>
        </>
      ),
    },
    {
      id: "logstash_conf",
      name: "pipeline/logstash.conf",
      content: (
        <>
          <EuiSpacer size="m" />
          <EuiText size="s" color="subdued">
            <p>
              Save as <code>pipeline/logstash.conf</code> alongside <code>docker-compose.yml</code>.
            </p>
          </EuiText>
          <EuiSpacer size="m" />
          <EuiCodeBlock
            language="ruby"
            fontSize="s"
            paddingSize="m"
            isCopyable
            overflowHeight={400}
          >
            {genLogstashConf(sourceEndpoint, selectedIndices, batchSize)}
          </EuiCodeBlock>
        </>
      ),
    },
    {
      id: "run",
      name: "Run",
      content: (
        <>
          <EuiSpacer size="m" />
          <EuiCodeBlock language="bash" fontSize="s" paddingSize="m" isCopyable>
            {`# Start the migration pipeline
docker compose up -d

# Watch progress
docker compose logs -f logstash`}
          </EuiCodeBlock>
        </>
      ),
    },
  ];

  // ── Kafka tabs (no VPC) ──────────────────────────────────────────────────
  const kafkaTabs = [
    {
      id: "source_connector",
      name: "Source Connector",
      content: (
        <>
          <EuiSpacer size="m" />
          <EuiText size="s" color="subdued">
            <p>Register this connector on your Kafka Connect cluster to read from OpenSearch.</p>
          </EuiText>
          <EuiSpacer size="m" />
          <EuiCodeBlock
            language="json"
            fontSize="s"
            paddingSize="m"
            isCopyable
            overflowHeight={300}
          >
            {genKafkaSourceConnector(sourceEndpoint, selectedIndices)}
          </EuiCodeBlock>
        </>
      ),
    },
    {
      id: "sink_connector",
      name: "Sink Connector",
      content: (
        <>
          <EuiSpacer size="m" />
          <EuiText size="s" color="subdued">
            <p>Register this connector to write consumed messages into Elasticsearch.</p>
          </EuiText>
          <EuiSpacer size="m" />
          <EuiCodeBlock
            language="json"
            fontSize="s"
            paddingSize="m"
            isCopyable
            overflowHeight={300}
          >
            {genKafkaSinkConnector(targetUrl, targetApiKey, selectedIndices)}
          </EuiCodeBlock>
        </>
      ),
    },
  ];

  return (
    <>
      <EuiTitle size="s">
        <h2>Execute Migration</h2>
      </EuiTitle>
      <EuiText color="subdued" size="s">
        <p>
          Your migration configuration is ready. Follow the steps below for{" "}
          <strong>{methodLabel[migrationMethod]}</strong>.
        </p>
      </EuiText>
      <EuiSpacer size="l" />

      {/* ── Summary panel ─────────────────────────────────────────────── */}
      <EuiPanel color="subdued" hasBorder paddingSize="m">
        <EuiFlexGroup gutterSize="xl" wrap responsive={false}>
          <EuiFlexItem grow={false}>
            <EuiText size="s">
              <p>
                <strong>Source</strong>
                <br />
                <code>{sourceEndpoint || "—"}</code>
              </p>
            </EuiText>
          </EuiFlexItem>
          <EuiFlexItem grow={false}>
            <EuiText size="s">
              <p>
                <strong>Target</strong>
                <br />
                <code>{effectiveTargetUrl || "—"}</code>
                {isVpcProxy && proxyEndpoint && (
                  <>
                    <br />
                    <EuiBadge color="warning" style={{ marginTop: 4 }}>
                      via VPC Proxy
                    </EuiBadge>
                  </>
                )}
              </p>
            </EuiText>
          </EuiFlexItem>
          <EuiFlexItem grow={false}>
            <EuiText size="s">
              <p>
                <strong>Target Type</strong>
                <br />
                {targetTypeBadge(targetType)}
              </p>
            </EuiText>
          </EuiFlexItem>
          <EuiFlexItem grow={false}>
            <EuiText size="s">
              <p>
                <strong>Method</strong>
                <br />
                <EuiBadge color="primary">{methodLabel[migrationMethod]}</EuiBadge>
              </p>
            </EuiText>
          </EuiFlexItem>
          <EuiFlexItem grow={false}>
            <EuiText size="s">
              <p>
                <strong>Indices</strong>
                <br />
                {selectedIndices.length} selected
              </p>
            </EuiText>
          </EuiFlexItem>
          <EuiFlexItem grow={false}>
            <EuiText size="s">
              <p>
                <strong>Batch / Slices</strong>
                <br />
                {batchSize} / {slices}
              </p>
            </EuiText>
          </EuiFlexItem>
        </EuiFlexGroup>
      </EuiPanel>

      <EuiSpacer size="l" />

      {/* ── remote_reindex ────────────────────────────────────────────── */}
      {migrationMethod === "remote_reindex" && (
        <>
          {isVpcProxy && (
            <>
              {proxyEndpoint ? (
                <EuiCallOut
                  title={`VPC Proxy active — routing through ${proxyEndpoint}`}
                  color="success"
                  iconType="check"
                  size="s"
                />
              ) : (
                <EuiCallOut
                  title="VPC Proxy endpoint not configured"
                  color="warning"
                  iconType="warning"
                  size="s"
                >
                  <p>
                    Go back to the <strong>Deploy Proxy</strong> step to complete the CloudFormation
                    deployment and enter the proxy endpoint.
                  </p>
                </EuiCallOut>
              )}
              <EuiSpacer size="m" />
            </>
          )}
          <EuiTabbedContent tabs={remoteReindexTabs} initialSelectedTab={remoteReindexTabs[0]} />
        </>
      )}

      {/* ── logstash, no VPC ─────────────────────────────────────────── */}
      {migrationMethod === "logstash" && !isVpcProxy && (
        <EuiTabbedContent tabs={logstashTabs} initialSelectedTab={logstashTabs[0]} />
      )}

      {/* ── logstash + VPC ───────────────────────────────────────────── */}
      {migrationMethod === "logstash" && isVpcProxy && (
        <>
          <EuiCallOut
            color="primary"
            title="Logstash will run on EC2 via Chef — no local Docker needed"
            iconType="pipelineApp"
          />
          <EuiSpacer size="m" />
          <EuiCodeBlock
            language="bash"
            fontSize="s"
            paddingSize="m"
            isCopyable
            overflowHeight={300}
          >
            {genLogstashVpcCfn({
              vpcId,
              subnetId,
              sourceEndpoint,
              sourceAuthType,
              sourceUsername,
              targetUrl,
              targetApiKey,
              indices: selectedIndices,
              batchSize,
              instanceType,
              allowedCidr,
              sourceRegion,
            })}
          </EuiCodeBlock>
          <EuiSpacer size="m" />
          <EuiText size="s" color="subdued">
            <p>
              Chef installs Logstash and starts the migration pipeline automatically. Monitor
              progress via CloudWatch:
            </p>
          </EuiText>
          <EuiSpacer size="s" />
          <EuiCodeBlock language="bash" fontSize="s" paddingSize="m" isCopyable>
            {`# Tail migration logs
aws logs tail /migration/logstash --follow --region ${sourceRegion}

# Check Logstash status via SSM (no SSH needed)
aws ssm start-session --target <INSTANCE_ID> --region ${sourceRegion}
sudo systemctl status logstash
sudo tail -f /var/log/logstash/logstash-plain.log`}
          </EuiCodeBlock>
        </>
      )}

      {/* ── kafka, no VPC ────────────────────────────────────────────── */}
      {migrationMethod === "kafka" && !isVpcProxy && (
        <>
          <EuiCallOut title="Prerequisites" color="primary" iconType="cluster" size="s">
            <p>
              Requires a running Kafka cluster with Kafka Connect and the{" "}
              <strong>Elasticsearch Sink Connector</strong> and an{" "}
              <strong>OpenSearch Source Connector</strong> installed.
            </p>
          </EuiCallOut>
          <EuiSpacer size="m" />
          <EuiTabbedContent tabs={kafkaTabs} initialSelectedTab={kafkaTabs[0]} />
        </>
      )}

      {/* ── kafka + VPC ──────────────────────────────────────────────── */}
      {migrationMethod === "kafka" && isVpcProxy && (
        <>
          <EuiCallOut
            color="primary"
            title="Kafka Connect will run on EC2 via Chef — connectors are auto-configured on deploy"
            iconType="cluster"
          />
          <EuiSpacer size="m" />
          <EuiCodeBlock
            language="bash"
            fontSize="s"
            paddingSize="m"
            isCopyable
            overflowHeight={300}
          >
            {genKafkaVpcCfn({
              vpcId,
              subnetId,
              sourceEndpoint,
              targetUrl,
              targetApiKey,
              indices: selectedIndices,
              instanceType,
              allowedCidr,
              sourceRegion,
            })}
          </EuiCodeBlock>
          <EuiSpacer size="m" />
          <EuiText size="s" color="subdued">
            <p>
              Chef installs Kafka Connect and configures source and sink connectors automatically.
            </p>
          </EuiText>
        </>
      )}

      <EuiSpacer size="l" />
      <EuiHorizontalRule />
      <EuiSpacer size="m" />

      {/* ── Monitor reindex tasks (all remote_reindex modes) ────────── */}
      {migrationMethod === "remote_reindex" && (
        <EuiAccordion
          id="monitor"
          buttonContent={
            <EuiFlexGroup gutterSize="s" alignItems="center" responsive={false}>
              <EuiFlexItem grow={false}>
                <EuiIcon type="inspect" />
              </EuiFlexItem>
              <EuiFlexItem>
                <EuiText size="s">
                  <strong>Monitor Reindex Progress</strong>
                </EuiText>
              </EuiFlexItem>
            </EuiFlexGroup>
          }
          paddingSize="m"
        >
          <EuiCodeBlock language="bash" fontSize="s" paddingSize="s" isCopyable>
            {`# List all running reindex tasks
curl -X GET "${effectiveTargetUrl}/_tasks?actions=*reindex&detailed&pretty" \\
  -H "Authorization: ApiKey ${targetApiKey}"

# Check document count on target index
curl -X GET "${effectiveTargetUrl}/<INDEX_NAME>/_count" \\
  -H "Authorization: ApiKey ${targetApiKey}"

# Cancel a specific task if needed
curl -X POST "${effectiveTargetUrl}/_tasks/<TASK_ID>/_cancel" \\
  -H "Authorization: ApiKey ${targetApiKey}"`}
          </EuiCodeBlock>
        </EuiAccordion>
      )}

      <EuiSpacer size="l" />

      <EuiFlexGroup gutterSize="m" responsive={false}>
        <EuiFlexItem grow={false}>
          <EuiButton iconType="arrowLeft" onClick={onBack}>
            Back
          </EuiButton>
        </EuiFlexItem>
      </EuiFlexGroup>
    </>
  );
}
