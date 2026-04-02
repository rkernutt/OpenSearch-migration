import { useState } from "react";
import {
  EuiTitle,
  EuiSpacer,
  EuiButton,
  EuiButtonEmpty,
  EuiFlexGroup,
  EuiFlexItem,
  EuiText,
  EuiCallOut,
  EuiPanel,
  EuiCodeBlock,
  EuiTabbedContent,
  EuiAccordion,
  EuiCopy,
  EuiBadge,
  EuiHorizontalRule,
  EuiIcon,
} from "@elastic/eui";
import type { MigrationMethod } from "./MethodPage";
import type { IndexInfo } from "./IndicesPage";

interface ExecutePageProps {
  sourceEndpoint: string;
  sourceRegion: string;
  sourceAuthType: "iam" | "basic";
  sourceUsername: string;
  sourcePassword: string;
  targetUrl: string;
  targetApiKey: string;
  migrationMethod: MigrationMethod;
  selectedIndices: string[];
  availableIndices: IndexInfo[];
  batchSize: number;
  slices: string;
  onBack: () => void;
}

// ── Config generators ─────────────────────────────────────────────────────────

function genRemoteReindexDevTools(
  sourceEndpoint: string,
  targetApiKey: string,
  indices: string[],
  batchSize: number,
  slices: string
): string {
  const bodies = indices.map((idx) => {
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
  });
  return bodies.join("\n\n");
}

function genRemoteReindexCurl(
  sourceEndpoint: string,
  targetUrl: string,
  targetApiKey: string,
  indices: string[],
  batchSize: number,
  slices: string
): string {
  return indices
    .map((idx) => {
      const slicesVal = slices === "auto" ? "auto" : slices;
      return `curl -X POST "${targetUrl}/_reindex?slices=${slicesVal}&wait_for_completion=false" \\
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
  indices: string[]
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

function genLogstashConf(
  sourceEndpoint: string,
  indices: string[],
  batchSize: number
): string {
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

function genKafkaSourceConnector(
  sourceEndpoint: string,
  indices: string[]
): string {
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

function genKafkaSinkConnector(
  targetUrl: string,
  targetApiKey: string,
  indices: string[]
): string {
  return `{
  "name": "elasticsearch-sink-connector",
  "config": {
    "connector.class": "io.confluent.connect.elasticsearch.ElasticsearchSinkConnector",
    "tasks.max": "1",
    "topics": "${indices.map((i) => `migration.${i}`).join(",")}",
    "connection.url": "${targetUrl}",
    "connection.username": "elastic",
    "connection.password": "<FROM_API_KEY>",
    "type.name": "_doc",
    "key.ignore": "true",
    "schema.ignore": "true",
    "drop.invalid.message": "false"
  }
}`;
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
  migrationMethod,
  selectedIndices,
  availableIndices,
  batchSize,
  slices,
  onBack,
}: ExecutePageProps) {
  const [copied, setCopied] = useState<string | null>(null);

  function handleCopy(key: string) {
    setCopied(key);
    setTimeout(() => setCopied(null), 2000);
  }

  const methodLabel: Record<MigrationMethod, string> = {
    remote_reindex: "Remote Reindex",
    logstash: "Logstash Pipeline",
    kafka: "Kafka Bridge",
    vpc_proxy: "VPC Proxy + Reindex",
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
              Paste this into <strong>Kibana → Dev Tools</strong> on your Elastic target cluster.
              Each POST starts an async reindex task; monitor with{" "}
              <code>GET _tasks?actions=*reindex&detailed</code>.
            </p>
          </EuiText>
          <EuiSpacer size="m" />
          <EuiCopy
            textToCopy={genRemoteReindexDevTools(
              sourceEndpoint,
              targetApiKey,
              selectedIndices,
              batchSize,
              slices
            )}
          >
            {(copy) => (
              <EuiButtonEmpty
                size="s"
                iconType={copied === "devtools" ? "check" : "copyClipboard"}
                onClick={() => { copy(); handleCopy("devtools"); }}
              >
                {copied === "devtools" ? "Copied!" : "Copy"}
              </EuiButtonEmpty>
            )}
          </EuiCopy>
          <EuiCodeBlock language="json" fontSize="s" paddingSize="m" isCopyable={false} overflowHeight={400}>
            {genRemoteReindexDevTools(sourceEndpoint, targetApiKey, selectedIndices, batchSize, slices)}
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
            <p>
              Run these commands from any machine with network access to both clusters.
            </p>
          </EuiText>
          <EuiSpacer size="m" />
          <EuiCodeBlock language="bash" fontSize="s" paddingSize="m" isCopyable overflowHeight={400}>
            {genRemoteReindexCurl(sourceEndpoint, targetUrl, targetApiKey, selectedIndices, batchSize, slices)}
          </EuiCodeBlock>
        </>
      ),
    },
  ];

  // ── Logstash tabs ────────────────────────────────────────────────────────
  const logstashTabs = [
    {
      id: "compose",
      name: "docker-compose.yml",
      content: (
        <>
          <EuiSpacer size="m" />
          <EuiCodeBlock language="yaml" fontSize="s" paddingSize="m" isCopyable overflowHeight={300}>
            {genLogstashDockerCompose(sourceEndpoint, sourceAuthType, sourceUsername, sourcePassword, targetUrl, targetApiKey, selectedIndices)}
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
            <p>Save as <code>pipeline/logstash.conf</code> alongside <code>docker-compose.yml</code>.</p>
          </EuiText>
          <EuiSpacer size="m" />
          <EuiCodeBlock language="ruby" fontSize="s" paddingSize="m" isCopyable overflowHeight={400}>
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

  // ── Kafka tabs ───────────────────────────────────────────────────────────
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
          <EuiCodeBlock language="json" fontSize="s" paddingSize="m" isCopyable overflowHeight={300}>
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
          <EuiCodeBlock language="json" fontSize="s" paddingSize="m" isCopyable overflowHeight={300}>
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

      {/* Summary */}
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
                <code>{targetUrl || "—"}</code>
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

      {/* VPC Proxy Phase 2 notice */}
      {migrationMethod === "vpc_proxy" && (
        <>
          <EuiCallOut
            title="Step 1 — Deploy the VPC Proxy CloudFormation Stack"
            color="warning"
            iconType="securityApp"
          >
            <EuiText size="s">
              <p>
                Before running the reindex, deploy the proxy CloudFormation template. It will
                create an EC2-based nginx proxy inside your VPC that forwards traffic to Elastic
                Cloud.
              </p>
            </EuiText>
            <EuiSpacer size="s" />
            <EuiCodeBlock language="bash" fontSize="s" paddingSize="s" isCopyable>
              {`# Deploy the VPC proxy stack (from the iac/cloudformation/ directory)
aws cloudformation deploy \\
  --template-file iac/cloudformation/vpc-proxy.yaml \\
  --stack-name opensearch-migration-proxy \\
  --parameter-overrides \\
    VpcId=<YOUR_VPC_ID> \\
    SubnetId=<PRIVATE_SUBNET_ID> \\
    ElasticCloudEndpoint=${targetUrl} \\
  --capabilities CAPABILITY_IAM \\
  --region ${sourceRegion}

# Get the proxy endpoint
aws cloudformation describe-stacks \\
  --stack-name opensearch-migration-proxy \\
  --query "Stacks[0].Outputs[?OutputKey=='ProxyEndpoint'].OutputValue" \\
  --output text`}
            </EuiCodeBlock>
            <EuiSpacer size="s" />
            <EuiText size="xs" color="subdued">
              <p>
                <EuiIcon type="iInCircle" size="s" /> Then use the proxy endpoint in place of{" "}
                <code>{targetUrl}</code> in the reindex commands below.
              </p>
            </EuiText>
          </EuiCallOut>
          <EuiSpacer size="l" />
          <EuiTitle size="xs">
            <h3>Step 2 — Run Remote Reindex via Proxy</h3>
          </EuiTitle>
          <EuiSpacer size="m" />
        </>
      )}

      {/* Method-specific config */}
      {(migrationMethod === "remote_reindex" || migrationMethod === "vpc_proxy") && (
        <EuiTabbedContent tabs={remoteReindexTabs} initialSelectedTab={remoteReindexTabs[0]} />
      )}

      {migrationMethod === "logstash" && (
        <EuiTabbedContent tabs={logstashTabs} initialSelectedTab={logstashTabs[0]} />
      )}

      {migrationMethod === "kafka" && (
        <>
          <EuiCallOut
            title="Prerequisites"
            color="primary"
            iconType="cluster"
            size="s"
          >
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

      <EuiSpacer size="l" />
      <EuiHorizontalRule />
      <EuiSpacer size="m" />

      {/* Monitor reindex tasks */}
      {(migrationMethod === "remote_reindex" || migrationMethod === "vpc_proxy") && (
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
curl -X GET "${targetUrl}/_tasks?actions=*reindex&detailed&pretty" \\
  -H "Authorization: ApiKey ${targetApiKey}"

# Check document count on target index
curl -X GET "${targetUrl}/<INDEX_NAME>/_count" \\
  -H "Authorization: ApiKey ${targetApiKey}"

# Cancel a specific task if needed
curl -X POST "${targetUrl}/_tasks/<TASK_ID>/_cancel" \\
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
