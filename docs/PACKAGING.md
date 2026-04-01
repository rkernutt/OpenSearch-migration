# Packaging: Docker vs local install

New users should configure `.env` first—see [GETTING_STARTED.md](GETTING_STARTED.md) and [examples/env](../examples/env/).

The **canonical** way to run the sample Logstash migration stack is **Docker Compose** in [Logstash_input](../Logstash_input/) (pinned image in [Logstash_input/Dockerfile](../Logstash_input/Dockerfile)). Some organizations cannot use Docker on migration jump hosts; use **one set of pipeline configs** and either Compose or a **local Logstash package** install.

## Docker Compose (recommended)

- From `Logstash_input/`: copy repo-root [`.env.example`](../.env.example) to `.env`, edit variables, run `docker compose up --build`.
- See [Logstash_input/README.md](../Logstash_input/README.md) for the `apikey` profile and proxy usage.
- **Pin versions:** rely on the Dockerfile `FROM logstash:...` tag; rebuild when upgrading.

## Local Logstash (no Docker)

1. Install a **Logstash version compatible** with your `logstash-input-opensearch` plugin (see [Elastic support matrix](https://www.elastic.co/support/matrix#matrix_logstash) and plugin docs).
2. Install the plugin, e.g. `bin/logstash-plugin install logstash-input-opensearch` (exact command per Logstash major).
3. Copy `Logstash_input/pipeline/logstash.conf` (or `logstash_api_key.conf`) to the host, e.g. `/etc/logstash/conf.d/migration.conf`.
4. Replace `${VAR}` placeholders—Logstash resolves environment variables from **`$VAR`** in pipelines when configured, or use static values:
   - Either export variables before `systemctl start logstash`, or
   - Use `EnvironmentFile=/etc/logstash/migration.env` in **systemd** for the Logstash unit, mirroring `.env` keys (`SOURCE_OPENSEARCH_HOST`, etc.).
5. Set JVM heap if needed: `LS_JAVA_OPTS="-Xmx4g"` (or `/etc/logstash/jvm.options`).

**Single source of truth:** Keep the repo’s `pipeline/*.conf` as the reference; sync to servers via git or config management rather than forking logic.

## Kafka-related tooling

- **Development only:** optional Compose stacks for Kafka/Zookeeper or Redpanda are **not** shipped in this repo by default; use your platform’s Kafka or a minimal dev compose if you adopt [KAFKA_MIGRATION.md](KAFKA_MIGRATION.md).
- **Brokers in production:** use vendor packages or managed Kafka; clients and Connect workers are typically installed from the same policy as Logstash (RPM/DEB or containers).

## CI and reproducibility

Prefer **Docker** in CI to run the same image as operators. Local-install testing can use a disposable VM with the same Logstash major as production.
