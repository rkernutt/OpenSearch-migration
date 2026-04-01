#!/usr/bin/env python3
"""
Preflight checks: OpenSearch and Elasticsearch endpoints respond and (optionally)
indices exist before long-running reindex or Logstash jobs.

Loads repo-root .env via bootstrap_env (same as validate_migration.py).
Exit codes with --strict-exit-codes: 0=ok, 2=misconfiguration, 3=network/auth HTTP failure.
Without --strict-exit-codes: 0=ok, 1=failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import requests

try:
    import boto3
except ImportError:
    boto3 = None


def main() -> None:
    import bootstrap_env

    bootstrap_env.load()

    from validate_migration import DestAuth, opensearch_auth_sigv4

    parser = argparse.ArgumentParser(
        description="Preflight: ping OpenSearch + Elastic; optional index HEAD and optional count equality."
    )
    parser.add_argument(
        "--source-host",
        default=os.environ.get("SOURCE_OPENSEARCH_HOST"),
        help="OpenSearch base URL",
    )
    parser.add_argument(
        "--dest-host", default=os.environ.get("DEST_ELASTIC_HOST"), help="Elastic base URL"
    )
    parser.add_argument(
        "--source-region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS region (SigV4)",
    )
    parser.add_argument(
        "--source-user",
        default=os.environ.get("SOURCE_OPENSEARCH_USER"),
        help="OpenSearch user (basic)",
    )
    parser.add_argument(
        "--source-password",
        default=os.environ.get("SOURCE_OPENSEARCH_PASSWORD"),
        help="OpenSearch password",
    )
    parser.add_argument(
        "--dest-api-key", default=os.environ.get("DEST_ELASTIC_API_KEY"), help="Elastic API key"
    )
    parser.add_argument(
        "--dest-user", default=os.environ.get("DEST_ELASTIC_USER"), help="Elastic user"
    )
    parser.add_argument(
        "--dest-password", default=os.environ.get("DEST_ELASTIC_PASSWORD"), help="Elastic password"
    )
    parser.add_argument(
        "--source-index",
        default=None,
        help="If set, HEAD source index and optionally compare counts",
    )
    parser.add_argument(
        "--dest-index",
        default=None,
        help="If set, HEAD dest index (requires --source-index for count check)",
    )
    parser.add_argument(
        "--check-counts",
        action="store_true",
        help="With both indices set, require _count to match (strict validation-lite).",
    )
    parser.add_argument(
        "--output-format",
        choices=("text", "json"),
        default="text",
        help="text: human-readable; json: one object on stdout",
    )
    parser.add_argument(
        "--strict-exit-codes",
        action="store_true",
        help="0=ok, 2=misconfiguration, 3=HTTP/network failure (default: any failure=1).",
    )
    args = parser.parse_args()

    def fail(msg: str, transport: bool = False) -> None:
        if args.output_format == "json":
            print(json.dumps({"ok": False, "error": msg, "transport": transport}))
        else:
            print(f"FAIL: {msg}", file=sys.stderr)
        if args.strict_exit_codes:
            sys.exit(3 if transport else 1)
        sys.exit(1)

    def ok_result(rows: list) -> None:
        if args.output_format == "json":
            print(json.dumps({"ok": True, "checks": rows}, indent=2))
        else:
            for line in rows:
                print(line)

    if not args.source_host or not args.dest_host:
        if args.output_format == "json":
            print(json.dumps({"ok": False, "error": "missing --source-host / --dest-host or env"}))
        else:
            print(
                "Error: set SOURCE_OPENSEARCH_HOST and DEST_ELASTIC_HOST (or flags).",
                file=sys.stderr,
            )
        sys.exit(2 if args.strict_exit_codes else 1)

    use_sigv4 = not (args.source_user and args.source_password)
    if use_sigv4 and not boto3:
        print("Error: SigV4 requires boto3.", file=sys.stderr)
        sys.exit(2 if args.strict_exit_codes else 1)
    if not args.dest_api_key and not (args.dest_user and args.dest_password):
        print("Error: set Elastic API key or user+password.", file=sys.stderr)
        sys.exit(2 if args.strict_exit_codes else 1)

    if args.check_counts and (not args.source_index or not args.dest_index):
        print("Error: --check-counts requires --source-index and --dest-index.", file=sys.stderr)
        sys.exit(2 if args.strict_exit_codes else 1)

    dest_auth = DestAuth(
        api_key=args.dest_api_key,
        user=args.dest_user,
        password=args.dest_password,
    )
    rows: list = []

    def get_os(path: str, **kwargs: Any) -> requests.Response:
        url = args.source_host.rstrip("/") + path
        if use_sigv4:
            auth = opensearch_auth_sigv4(args.source_region)
            return requests.get(url, auth=auth, timeout=30, **kwargs)
        return requests.get(
            url, auth=(args.source_user, args.source_password), timeout=30, **kwargs
        )

    def get_es(path: str) -> requests.Response:
        url = args.dest_host.rstrip("/") + path
        headers, auth = dest_auth.apply()
        return requests.get(url, headers=headers, auth=auth, timeout=30)

    try:
        r_os = get_os("/")
        r_os.raise_for_status()
        rows.append(f"OpenSearch ping OK ({r_os.status_code})")

        r_es = get_es("/")
        r_es.raise_for_status()
        rows.append(f"Elasticsearch ping OK ({r_es.status_code})")

        if args.source_index:
            url = args.source_host.rstrip("/") + "/" + args.source_index
            if use_sigv4:
                auth = opensearch_auth_sigv4(args.source_region)
                h = requests.head(url, auth=auth, timeout=30)
            else:
                h = requests.head(url, auth=(args.source_user, args.source_password), timeout=30)
            if h.status_code == 404:
                fail(f"source index missing: {args.source_index}")
            h.raise_for_status()
            rows.append(f"OpenSearch index exists: {args.source_index}")

        if args.dest_index:
            url = args.dest_host.rstrip("/") + "/" + args.dest_index
            headers, auth = dest_auth.apply()
            h = requests.head(url, headers=headers, auth=auth, timeout=30)
            if h.status_code == 404:
                fail(f"destination index missing: {args.dest_index}")
            h.raise_for_status()
            rows.append(f"Elasticsearch index exists: {args.dest_index}")

        if args.check_counts and args.source_index and args.dest_index:
            from validate_migration import (
                get_count_elastic,
                get_count_opensearch_basic,
                get_count_opensearch_sigv4,
            )

            if use_sigv4:
                sc = get_count_opensearch_sigv4(
                    args.source_host, args.source_index, args.source_region
                )
            else:
                sc = get_count_opensearch_basic(
                    args.source_host, args.source_index, args.source_user, args.source_password
                )
            dc = get_count_elastic(args.dest_host, args.dest_index, dest_auth)
            if sc != dc:
                fail(f"count mismatch: source={sc} dest={dc}")
            rows.append(f"counts match: {sc}")

    except requests.RequestException as e:
        msg = str(e)
        if hasattr(e, "response") and e.response is not None:
            msg += f" — {e.response.text[:300]}"
        fail(msg, transport=True)

    ok_result(rows)


if __name__ == "__main__":
    main()
