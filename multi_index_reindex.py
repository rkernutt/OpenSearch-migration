#!/usr/bin/env python3
"""
Generate reindex requests or Logstash run commands for multiple indices.
Use the output in Kibana Dev Tools (reindex) or run Logstash once per index.
Does not call OpenSearch or Elastic; it only generates config.
"""

import argparse
import json
import os
import sys
from typing import List

REINDEX_TEMPLATE = """POST _reindex
{body}
"""

REINDEX_TEMPLATE_LARGE = """POST _reindex?scroll=10m&wait_for_completion=false
{body}
"""


def parse_indices_arg(indices: str) -> List[str]:
    return [s.strip() for s in indices.split(",") if s.strip()]


def read_indices_file(path: str) -> List[str]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def main():
    import bootstrap_env

    bootstrap_env.load()

    parser = argparse.ArgumentParser(
        description="Generate reindex requests or index list for multi-index migration."
    )
    parser.add_argument(
        "--indices",
        type=str,
        default=None,
        help="Comma-separated list of source index names (e.g. logs-2024,metrics-2024).",
    )
    parser.add_argument(
        "--indices-file",
        type=str,
        default=None,
        help="File with one source index name per line (# starts a comment).",
    )
    parser.add_argument(
        "--source-host",
        type=str,
        default=(
            os.environ.get("SOURCE_OPENSEARCH_HOST")
            or "https://Amazon_Opensearch_Service_Domain_Endpoint:443"
        ),
        help="OpenSearch host for reindex source.remote.host (env: SOURCE_OPENSEARCH_HOST).",
    )
    parser.add_argument(
        "--username",
        type=str,
        default=os.environ.get("SOURCE_OPENSEARCH_USER", "username"),
        help="OpenSearch username for reindex (env: SOURCE_OPENSEARCH_USER).",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=os.environ.get("SOURCE_OPENSEARCH_PASSWORD", "password"),
        help="OpenSearch password for reindex (env: SOURCE_OPENSEARCH_PASSWORD).",
    )
    parser.add_argument(
        "--dest-prefix",
        type=str,
        default=os.environ.get("MIGRATION_DEST_PREFIX", ""),
        help="Optional prefix for dest index names (env: MIGRATION_DEST_PREFIX).",
    )
    parser.add_argument(
        "--dest-suffix",
        type=str,
        default=os.environ.get("MIGRATION_DEST_SUFFIX", ""),
        help="Optional suffix for dest index names (env: MIGRATION_DEST_SUFFIX).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Write reindex requests to this file; otherwise print to stdout.",
    )
    parser.add_argument(
        "--format",
        choices=("devtools", "list"),
        default="devtools",
        help="devtools: multiple POST _reindex blocks for Kibana Dev Tools; list: one index per line for scripting.",
    )
    parser.add_argument(
        "--large",
        action="store_true",
        help="Use async large-dataset style: ?scroll=10m&wait_for_completion=false, socket_timeout, source size (see --reindex-batch-size).",
    )
    parser.add_argument(
        "--reindex-batch-size",
        type=int,
        default=500,
        help="With --large: value for source.size in each reindex body (default 500).",
    )
    parser.add_argument(
        "--socket-timeout",
        type=str,
        default="60m",
        help="With --large: source.remote.socket_timeout (default 60m).",
    )
    parser.add_argument(
        "--mask-credentials",
        action="store_true",
        help="Replace username and password in generated output with '***' (safe for logs/CI artifacts).",
    )
    args = parser.parse_args()

    indices: List[str] = []
    if args.indices:
        indices.extend(parse_indices_arg(args.indices))
    if args.indices_file:
        indices.extend(read_indices_file(args.indices_file))

    # De-dupe preserving order
    seen = set()
    indices = [x for x in indices if not (x in seen or seen.add(x))]

    if not indices:
        print(
            "Error: provide --indices and/or --indices-file with at least one index.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.format == "list":
        out = "\n".join(indices) + "\n"
        if args.output:
            with open(args.output, "w") as f:
                f.write(out)
        else:
            print(out, end="")
        print("Use this list to run Logstash once per index (e.g. in a loop).", file=sys.stderr)
        return

    if not args.mask_credentials and args.password != "password":
        print(
            "WARNING: output contains plaintext credentials. Use --mask-credentials to redact, "
            "or --output <file> to write to a file instead of stdout.",
            file=sys.stderr,
        )

    tpl = REINDEX_TEMPLATE_LARGE if args.large else REINDEX_TEMPLATE
    blocks = []
    _username = "***" if args.mask_credentials else args.username
    _password = "***" if args.mask_credentials else args.password
    for idx in indices:
        dest_name = args.dest_prefix + idx + args.dest_suffix
        remote = {
            "host": args.source_host,
            "username": _username,
            "password": _password,
        }
        source: dict = {
            "remote": remote,
            "index": idx,
        }
        if args.large:
            remote["socket_timeout"] = args.socket_timeout
            source["size"] = args.reindex_batch_size

        body = {
            "source": source,
            "dest": {"index": dest_name},
        }
        blocks.append(tpl.format(body=json.dumps(body, indent=2)))

    out = "\n".join(blocks)
    if args.output:
        with open(args.output, "w") as f:
            f.write(out)
        print(
            f"Wrote {len(blocks)} reindex request(s) to {args.output}. Paste into Kibana Dev Tools.",
            file=sys.stderr,
        )
    else:
        print(out)

    if args.large:
        print(
            "Each request returns a task id in the response. Poll with: "
            "python poll_reindex_task.py --task-id <id>",
            file=sys.stderr,
        )
    print(
        "Validate each index with: python validate_migration.py --source-index <src> --dest-index <dest> ...",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
