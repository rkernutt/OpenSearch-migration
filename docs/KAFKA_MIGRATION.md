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
