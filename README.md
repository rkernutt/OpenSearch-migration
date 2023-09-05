# OSS migration to Elastic

OSS migration to Elastic helps to read the indexes stored on an OpenSearch cluster. This is useful for migrating or reindexing to Elastic clusters. This helps users to periodically ingest data into Elastic clusters, or migrate once.

There are several methods available:

Remote Reindex - performing a remote reindex from within Kibana
Logstash - running a logstash pipeline with the opensearch input
