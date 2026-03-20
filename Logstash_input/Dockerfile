# Logstash with logstash-input-opensearch; pipeline reads settings from environment (.env via Docker Compose).
ARG LOGSTASH_VERSION=8.12.2
FROM docker.elastic.co/logstash/logstash:${LOGSTASH_VERSION}

USER root

COPY pipeline/logstash.conf /usr/share/logstash/pipeline/logstash.conf
RUN chown logstash:root /usr/share/logstash/pipeline/logstash.conf

USER logstash
WORKDIR /usr/share/logstash

RUN bin/logstash-plugin install logstash-input-opensearch

# Default pipeline is pipeline/logstash.conf (Elastic Cloud cloud_id / cloud_auth).
# For API key output, override at runtime:
#   docker compose run --rm -v "$PWD/pipeline/logstash_api_key.conf:/usr/share/logstash/pipeline/logstash.conf:ro" logstash
