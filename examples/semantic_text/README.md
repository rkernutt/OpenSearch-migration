# semantic_text examples (Elastic destination)

Replace placeholders before use:

- `REPLACE_WITH_INFERENCE_ENDPOINT_ID` — id of an **inference endpoint** on your Elastic cluster ([Inference API docs](https://www.elastic.co/docs/explore-analyze/elastic-inference)).
- `REPLACE_WITH_INDEX_NAME` — destination index name.

Validate JSON against your **target Elastic version**; field parameters evolve between minors.

Files:

- `destination_index_mapping.json` — index template with a `semantic_text` field.
- `ingest_pipeline_inference.json` — optional pattern using an `inference` processor (syntax varies by version; use as a starting point only).
