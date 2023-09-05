# OpenSearch migration to Elastic

OpenSearch migration to Elastic is project that helps you read the indexes stored on an OpenSearch cluster. This is useful for migrating or reindexing to Elastic clusters. This helps users to periodically ingest data into Elastic clusters, or migrate once and retire OpenSearch.

There are several methods available:

Remote_Reindex - performing a remote reindex from within Kibana

Logstash - running a logstash pipeline with the opensearch input
