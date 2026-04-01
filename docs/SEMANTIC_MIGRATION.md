# Phase 2: Semantic and vector fields (OpenSearch → Elastic)

Elasticsearch **`semantic_text`** (8.15+, evolved in later 8.x/9.x) bundles **chunking**, **inference**, and vector search behind one field type. OpenSearch offers **`knn_vector`**, **neural / ML** workflows, and from **3.1** a **`semantic`** field type with a different model and query surface. There is **no byte-for-byte** migration of `semantic_text` or OpenSearch `semantic` internals—plan for **re-embedding** or **conditional vector copy**.

## 1. Inventory the source index

For each index, capture from `_mapping` and any ingest pipelines:

| OpenSearch artifact | Notes for Elastic |
|---------------------|-------------------|
| `knn_vector` / `nested` vectors | Dimensions, similarity (`cosinesimil`, `l2`, `innerproduct`), element type; maps to Elastic `dense_vector` if you keep raw vectors. |
| `text` + separate vector field | Often re-run **inference** on Elastic from text into `semantic_text` or `dense_vector`. |
| Neural / `model_id` in mapping or search | Record **model id**, version; embeddings are usually **not portable** across stacks unless the **same model** and **same preprocessing** exist on Elastic. |
| **`semantic`** field (OpenSearch 3.1+) | Typically tied to a **deployed model** on OpenSearch; plan to **read raw text** from `_source` if stored and **embed on Elastic** with your chosen inference endpoint. |

**Script idea:** `GET index/_mapping` plus grep/categorize field types; note pipeline attachments on `index.default_pipeline`.

## 2. Migration strategies (choose per index)

| Source | Suggested destination | Notes |
|--------|----------------------|--------|
| Raw text available | `semantic_text` + **inference endpoint** | Preferred for new Elastic-native semantics and hybrid queries. |
| Only stored vectors; same dim/metric as an Elastic model | `dense_vector` (optional copy of array) | Validate **space type** and **element_type**; re-embed if models differ. |
| OpenSearch `semantic` field | Treat as **opaque**; use **source text** + Elastic inference | Do not assume chunking matches. |

**Hosted vs Serverless:** `semantic_text` is available on current Elastic offerings; **remote reindex** is **not** available to **Elastic Cloud Serverless**—use **bulk / Logstash / Kafka** paths with **default or named ingest pipeline** on the destination ([SERVERLESS.md](SERVERLESS.md)).

## 3. Reference assets in this repo

- [examples/semantic_text/destination_index_mapping.json](../examples/semantic_text/destination_index_mapping.json) — sample `semantic_text` mapping; replace the inference id.
- [examples/semantic_text/ingest_pipeline_inference.json](../examples/semantic_text/ingest_pipeline_inference.json) — optional **inference** processor pattern for pipelines (adjust names to your stack and version).

Before production, validate against your **Elastic version** docs ([Inference API](https://www.elastic.co/docs/explore-analyze/elastic-inference), `semantic_text` mapping reference).

## 4. Operational flow (text → `semantic_text`)

1. On Elastic: create **inference endpoint** (managed model or service; org-dependent).
2. `PUT` destination index with `semantic_text` mapping referencing that endpoint (see example JSON).
3. Migrate documents without compatible vectors: **bulk** or pipeline with **`default_pipeline`** so `_source` text fields are embedded at ingest.
4. For **remote reindex** from OpenSearch: you may need a **`script`** to drop incompatible vector subfields and keep plain text, then rely on ingest for embeddings—or **reindex to an intermediate** index then **reindex again** into the final `semantic_text` mapping (pattern depends on whether `semantic_text` accepts your field paths).

## 5. Golden queries and parity

OpenSearch **neural / kNN** queries will not match Elastic **semantic / knn / retriever** DSL one-to-one. Define a **small golden set** before cutover:

1. **Queries:** 20–100 representative natural-language queries (and optional filters) used in production.
2. **Baseline:** Run on OpenSearch; store **top-k** document ids (and scores if comparable) per query.
3. **Candidate:** Run equivalent queries on Elastic (`semantic`, `knn`, or hybrid with RRF per your design).
4. **Metrics:** **Recall@k** / overlap of top-k ids (k = 5, 10, 20); spot-check **manual relevance** for regressions.
5. **Triage:** Mapping differences (chunk size, model, quantization) often explain drift; tune chunking/inference settings rather than expecting identical scores.

Record results in your migration ticket; pass/fail gates are a **business** decision, not automated in this repo.

## 6. Further reading

- [Elastic Labs: semantic_text vs OpenSearch semantic field](https://www.elastic.co/search-labs/blog/elasticsearch-semantic-text-vs-opensearch-semantic-field)
- [OpenSearch semantic field](https://docs.opensearch.org/latest/field-types/supported-field-types/semantic/)
