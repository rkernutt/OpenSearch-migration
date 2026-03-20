# Remote reindex (OpenSearch → Elastic Cloud)

Use the Elasticsearch reindex API from your **Elastic** deployment to pull data from a remote Amazon OpenSearch Service (or OpenSearch) domain.

## Prerequisites

- **Elastic Cloud Hosted** deployment (remote reindex is not supported on Elastic Cloud Serverless).
- The OpenSearch domain must be reachable from Elastic Cloud (public endpoint or proxy). If the domain is in a VPC with no public endpoint, use a public endpoint or run a proxy in AWS that Elastic can call (see [Proxy](../Proxy/README.md)).

## 1. Configure allowlist on Elastic Cloud

In the Elastic Cloud console: **Edit deployment** → **User settings** (or `elasticsearch.yml`). Add:

```yaml
reindex.remote.whitelist: ["search-your-opensearch-domain-xxxx.region.es.amazonaws.com:443"]
```

Use the exact host and port of your OpenSearch domain. For Amazon OpenSearch Service (public CA), no SSL certificate settings are required. Save and allow the deployment to apply changes.

## 2. (Optional) Create destination index with performance settings

For large reindexes, create the destination index first with relaxed refresh and no replicas. In Kibana Dev Tools on the **Elastic** deployment, run the contents of `Elastic_destination_index_settings.json` (replace `destinationindexname`). After reindex completes, update `refresh_interval` and `number_of_replicas` as desired.

## 3. Run reindex

- **Standard:** Use `Elastic_DEVTOOLS_reindex.json`. Replace host, username, password, source index name, and destination index name. Run in Dev Tools on the **Elastic** deployment. The `host` must include `https://`.
- **With Painless script** (drop/rename fields): Use `Elastic_DEVTOOLS_reindex_with_script.json` as a starting point; edit the `script.source` for your fields.
- **Large datasets:** Use `Elastic_DEVTOOLS_reindex_large.json` with the query params `?scroll=10m&wait_for_completion=false`. The response returns a `task` id; poll `GET _tasks/<task_id>` for progress. Tune `size` and `socket_timeout` as needed for large documents.
- **Many indices:** From the repo root, `python multi_index_reindex.py` can emit one Dev Tools block per index. Use `--indices-file` for a long list (one index per line, `#` comments). Add `--large` for the same async/large style as `Elastic_DEVTOOLS_reindex_large.json` (optional `--reindex-batch-size`, `--socket-timeout`).

For secrets and IAM when validating migrations, see [../SECURITY.md](../SECURITY.md) and [../.env.example](../.env.example).

## 4. Mappings and version conflicts

OpenSearch and Elasticsearch mappings are usually compatible, but dynamic templates, analyzers, or field types can differ.

- **Strict control:** On the **Elastic** cluster, `PUT` the destination index first with the mappings and settings you want, then run `_reindex` (the source data is applied to that mapping).
- **Let reindex define the index:** Omit `PUT` so the destination index is created from the remote mapping; adjust later if Elasticsearch rejects or remaps fields.
- **Field changes:** Use a `script` in the reindex body to drop, rename, or transform fields (see Elasticsearch reindex documentation).

**Version conflicts** when re-running or overlapping reindex:

- Default is **`conflicts: abort`** (or query param behavior that stops on conflict)—first conflict fails the task.
- Use **`?conflicts=proceed`** on `POST _reindex` to continue when the same `_id` already exists with a different version—useful for idempotent partial reruns. Example:

```http
POST _reindex?conflicts=proceed
```

(Combine with other query params as needed, e.g. `?scroll=10m&wait_for_completion=false&conflicts=proceed`.)

## 5. Async reindex and task polling

When you use `wait_for_completion=false` (see `Elastic_DEVTOOLS_reindex_large.json` or `multi_index_reindex.py --large`):

1. The JSON response includes a **`task`** string such as `node_id:task_id` or a task id; use it with **`GET _tasks/<task_id>`** (strip a `task:` prefix if present).
2. List active reindex tasks: **`GET _tasks?actions=*reindex`**
3. **CLI helper** (same `DEST_ELASTIC_HOST` / `DEST_ELASTIC_API_KEY` as validation):

```bash
python poll_reindex_task.py --task-id "<id-from-response>"
```

Optional: `--interval 5`, `--timeout 86400`, `--verbose`.
