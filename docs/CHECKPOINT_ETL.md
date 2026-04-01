# Checkpointed ETL vs Logstash (evaluation)

For **very large** indices or strict **resume-after-failure** requirements, a **small custom worker** that reads OpenSearch with **PIT + `search_after`** and writes **bulk** to Elastic can be easier to reason about than tuning Logstash alone.

## Logstash-only

**Strengths:** Ready-made `logstash-input-opensearch`, Elasticsearch output with retries, Docker path in this repo.

**Limits:**

- **Checkpoint:** OpenSearch input does not offer JDBC-style “bookmark” out of the box; resuming often means **re-running** with a **time-bounded query** or accepting overlap + idempotent `_id`.
- **Visibility:** Large pipelines need DLQ and heap tuning (see [Logstash_input/README.md](../Logstash_input/README.md)).

**When to stay with Logstash:** Team knows Logstash; index fits in one or few time-windowed passes; `conflicts=proceed` or idempotent reruns are acceptable.

## Custom checkpointed worker (PIT + `search_after`)

**Idea:** Persist `(pit_id, search_after, optional slice id)` to disk or a small metadata store; on restart, reopen PIT if still valid or open a new PIT and skip already-indexed ids if needed (by secondary index of completed ranges, or by deterministic time slices).

**Strengths:**

- **Explicit progress:** Query-level checkpoint after each successful bulk batch.
- **Throughput control:** Batch size, concurrency, and retries in code.
- **Kafka handoff:** Optionally publish batches to Kafka for downstream replay ([KAFKA_MIGRATION.md](KAFKA_MIGRATION.md)).

**Costs:** You maintain code (Python/Go/Java), handle PIT keepalive, parse `_bulk` errors, and test edge cases (mapping failures, shard relocation).

## Remote reindex (Elastic Hosted)

**Still the best default** for bulk **Hosted → OpenSearch** pulls: server-side parallelization, task API, no migration host CPU for scroll. Use [Remote_Reindex](../Remote_Reindex/) and [poll_reindex_task.py](../poll_reindex_task.py). Not available for **Elastic Serverless** as destination—use Logstash or custom ETL ([SERVERLESS.md](SERVERLESS.md)).

## Recommendation

| Scenario | Prefer |
|----------|--------|
| Elastic Cloud **Hosted**, network OK | **Remote reindex** + task polling |
| Elastic **Serverless** or need streaming buffer | **Logstash** or **Logstash → Kafka** → sink |
| Must minimize re-read of OpenSearch on failure | **Custom PIT worker** (optional Kafka) |

This repo does not ship a full worker implementation; treat this file as the decision record and sizing input for a follow-on ticket if you build one.
