# Kafka buffer: OpenSearch → Kafka → Elastic

Use this pattern when you need **durable replay**, **backpressure** independent of the source cluster, **multiple consumers** (e.g. production Elastic plus audit or a semantic re-embedding job), or **per-document ordering** keyed by `_id`.

**Scope:** This repository documents **reference architectures and operations**. It does not ship a production Kafka cluster or managed connectors; choose **Kafka Connect**, a **custom consumer**, or **Logstash ↔ Kafka** based on your license and ops model.

## When to use Kafka vs Logstash-only

| Need | Logstash-only | Kafka in the middle |
|------|---------------|---------------------|
| Lowest ops footprint | Prefer | Optional |
| Replay after sink failure without re-scrolling OpenSearch | Limited (DLQ + checkpoints) | Strong (topic retention + offsets) |
| Multiple downstream sinks | Usually one primary | Natural fan-out |
| Ordering for updates to the same document | `pipeline.workers => 1`, `pipeline.ordered => true` | **Partition key** = `_id` (or business key) |
| Spike absorption | Queue tuning | Broker buffers; scale consumers |

See [RUNBOOK.md](../RUNBOOK.md) for Logstash ordering and failure handling.

## Reference topologies

### 1. Custom harvester → Kafka → Elastic

1. **Extract:** A small service (or batch job) reads OpenSearch with **PIT + `search_after`** or **scroll** (prefer PIT for long runs). Serialize each hit as JSON (include `_id`, `_source`, optional `_routing`).
2. **Produce:** Publish to a topic with **key** = document `_id` so all updates for that id go to one partition (per-partition ordering).
3. **Load:** Consumer performs **bulk** `_bulk` to Elastic with the same `_id` (idempotent retries). Apply **ingest pipelines** on the destination for transforms (including `semantic_text` / inference—see [SEMANTIC_MIGRATION.md](SEMANTIC_MIGRATION.md)).

**Pros:** Full control over checkpoints (store last `sort` values per slice). **Cons:** You own the harvester code and consumer ops.

### 2. Logstash → Kafka → Logstash (or Connect)

- **First pipeline:** `opensearch` input → `kafka` output (serialize minimal fields; use **`message_key`** from `[@metadata][_id]` or a `ruby` filter).
- **Second pipeline:** `kafka` input → **`mutate`/filters** → `elasticsearch` output.

**Pros:** Reuses `logstash-input-opensearch`. **Cons:** Two Logstash footprints; still need sound Kafka keying for ordering.

### 3. Flink / Spark / enterprise ETL

Use Kafka as the **handoff** between extract and load stages for very large indices or centralized batch platforms. Same contract: keyed messages, idempotent bulk by `_id`.

## Document-level transform layer

Whichever topology you pick (custom harvester, Logstash-bridged, or Flink/Spark), the **same defensive transforms** apply to documents in flight. None of these address snapshot/segment-level OpenSearch ↔ Elasticsearch incompatibility — Kafka transports documents as JSON `_source`, not Lucene segments, so those concerns simply don't enter the pipeline. They address per-document metadata that **does** travel through `_search` results and **can** break ES `_bulk`.

| Concern | What to do |
|---------|-----------|
| Legacy `_type` from ES 5/6 sources (or any non-`_doc` type) | Replace with `"_doc"` before bulk-indexing. ES 8+ and Elastic Cloud Serverless reject anything else. |
| Source `_seq_no` / `_primary_term` | Never propagate. They're cluster-internal shard checkpoints; passing them through causes `version_conflict_engine_exception` on the destination. |
| Source `_version` | Ignore (default ES output behaviour). Setting `version_type => external` propagates stale source versions and causes the same conflict on retry. |
| `_id` preservation | Use the source `_id` as the destination `_id` for idempotent bulk replay. Also use it as the **Kafka partition key** so updates to the same id stay ordered. |
| `_routing` | Drop unless the destination uses the same routing scheme. Wrong-shard placement is a silent correctness bug. |
| OpenSearch-specific fields (`knn_vector`, `[opensearch]`, OS Observability `[event][module]`) | Drop on the producer side, or rename to a destination-friendly mapping. For k-NN, recommend re-embedding on the destination with an inference processor — see [SEMANTIC_MIGRATION.md](SEMANTIC_MIGRATION.md). |

### Logstash-as-consumer (topology 2)

The same hardened filter block used for direct OpenSearch → Elastic flows in [`Logstash_input/pipeline/logstash.conf`](../Logstash_input/pipeline/logstash.conf) works unchanged when the input is Kafka instead of OpenSearch — only the input section changes:

```ruby
input {
  kafka {
    bootstrap_servers => "${KAFKA_BOOTSTRAP}"
    topics            => ["${TOPIC}"]
    group_id          => "es-loader"
    codec             => "json"
    # The producer must set the Kafka message key to the source _id so we
    # can reuse it on the destination.
    decorate_events   => "extended"
  }
}

filter {
  # Promote the Kafka message key into [@metadata][_id] for the ES output.
  if [@metadata][kafka][key] {
    mutate { add_field => { "[@metadata][_id]" => "%{[@metadata][kafka][key]}" } }
  }

  # Same defensive transforms as Logstash_input/pipeline/logstash.conf:
  mutate {
    remove_field => [ "@version" ]
  }
  mutate {
    remove_field => [ "[@metadata][_type]" ]
    add_field    => { "[@metadata][_type]" => "_doc" }
  }
  # Optionally drop OpenSearch-specific fields here.
}

output {
  elasticsearch {
    hosts       => ["${DEST_ELASTIC_HOST}"]
    api_key     => "${DEST_ELASTIC_API_KEY}"
    index       => "${LOGSTASH_DEST_INDEX}"
    document_id => "%{[@metadata][_id]}"
    manage_template => false
    ilm_enabled     => false
    # Do NOT set version / version_type.
  }
}
```

The producer-side Logstash should set the Kafka message key to the source `_id`:

```ruby
input {
  opensearch {
    hosts    => "${SOURCE_OPENSEARCH_HOST}"
    user     => "${SOURCE_OPENSEARCH_USER}"
    password => "${SOURCE_OPENSEARCH_PASSWORD}"
    index    => "${LOGSTASH_SOURCE_INDEX}"
    docinfo  => true
  }
}
filter {
  mutate { remove_field => [ "@version" ] }
  # Force _doc here too, so the dest never sees a legacy type even if the
  # consumer pipeline forgets the same step.
  mutate {
    remove_field => [ "[@metadata][_type]" ]
    add_field    => { "[@metadata][_type]" => "_doc" }
  }
}
output {
  kafka {
    bootstrap_servers => "${KAFKA_BOOTSTRAP}"
    topic_id          => "${TOPIC}"
    codec             => "json"
    message_key       => "%{[@metadata][_id]}"
    # Idempotent producer where supported by your Kafka client.
    # acks              => "all"
  }
}
```

### Kafka Connect SMT equivalents (topology 1)

If you use the [Elasticsearch Sink Connector](https://www.confluent.io/hub/confluentinc/kafka-connect-elasticsearch) instead of Logstash on the consumer side, the same defensive moves are configured as Single Message Transforms (SMTs):

```properties
# connect-elasticsearch-sink.properties — partial
name=oss-to-es-sink
connector.class=io.confluent.connect.elasticsearch.ElasticsearchSinkConnector
topics=opensearch-docs
connection.url=https://your-deployment.es.region.aws.found.io
connection.api.key=${file:/etc/secrets/es.properties:api_key}

# Use the Kafka message key as the destination _id (idempotent retries,
# preserves source identity).
key.ignore=false

# Don't propagate Kafka schema metadata into the document.
schema.ignore=true

# Behaviour on data errors: log + skip vs fail. For migrations prefer
# logging so a single poison doc doesn't stop the consumer.
behavior.on.malformed.documents=warn
behavior.on.null.values=delete

# SMT chain: drop fields that would otherwise cause _bulk rejections.
transforms=dropMeta,dropType,dropOSPlugins
transforms.dropMeta.type=org.apache.kafka.connect.transforms.ReplaceField$Value
transforms.dropMeta.blacklist=_seq_no,_primary_term,_version,_score
transforms.dropType.type=org.apache.kafka.connect.transforms.ReplaceField$Value
transforms.dropType.blacklist=_type
transforms.dropOSPlugins.type=org.apache.kafka.connect.transforms.ReplaceField$Value
# Customise per source: knn_vector, opensearch.*, event.module, ...
transforms.dropOSPlugins.blacklist=knn_vector
```

If your destination index template is created with `"dynamic": "strict"`, also add a transform that whitelists exactly the fields your mapping accepts; it's the SMT equivalent of `metadata_migration`'s sanitiser.

### What this layer does **not** solve

Both the Logstash and Connect variants above handle per-document hygiene. They do **not** handle:

- **Mapping conflicts** between source and destination — solved by running [`metadata_migration`](../metadata_migration/) first so destination indices are pre-created with sanitized templates.
- **Forbidden Serverless settings** — solved by `metadata_migration --target-type ELASTICSEARCH_SERVERLESS`.
- **k-NN vector incompatibility** — solved by re-embedding on the destination; see [SEMANTIC_MIGRATION.md](SEMANTIC_MIGRATION.md).
- **Snapshot / Lucene segment version mismatches** — not applicable to any document-streaming path. If you need snapshot-level migration, use the wrapped Reindex-from-Snapshot path in [RFS.md](RFS.md).

## Ordering semantics

- Elasticsearch **shards** index in parallel; **global FIFO** across an entire index is not a product guarantee.
- **Per-document** ordering (latest update wins for a given `_id`): use a **single** logical writer per key—e.g. Kafka partition keyed by `_id`, or one Logstash worker with `pipeline.ordered => true`.
- Increasing **partition count** improves throughput but allows **concurrent** processing of different ids (by design).

## Reliability

- **Producers:** idempotent producer settings where supported; handle OpenSearch timeouts with bounded retries.
- **Consumers:** **at-least-once** delivery is typical; **idempotent indexing** using stable `_id` avoids duplicates on replay.
- **Dead-letter topic:** route poison messages (mapping explosions, oversize docs) for inspection; fix and replay manually.
- **Offset commits:** commit after successful bulk (or use transactions if your stack requires exactly-once end-to-end—often unnecessary if `_id` is stable).

## Topic and retention

- Size topics for **peak extract rate** and **slow-consumer** scenarios; retention should cover **replay window** after an outage (hours to days, per RPO).
- Consider **compaction** only if you truly want “latest value per key” and understand compaction lag; many migrations use **time-based retention** and a bounded replay job instead.

## Network and security

- OpenSearch in VPC: harvester or first-hop Logstash must reach the domain (or [Proxy](../Proxy/README.md)); Kafka and Elastic endpoints must be reachable from consumers.
- Encrypt in transit (TLS) and restrict IAM/network for brokers and Elastic.

## Validation

After load, use [validate_migration.py](../validate_migration.py) (counts and `_mget` sampling). For semantic indices, add **golden queries** per [SEMANTIC_MIGRATION.md](SEMANTIC_MIGRATION.md).
