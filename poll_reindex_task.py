#!/usr/bin/env python3
"""
Poll an Elasticsearch async task (e.g. from POST _reindex?wait_for_completion=false) until it completes.
Uses the same Elastic credentials as validate_migration.py (env or flags).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from typing import Any, Optional, Tuple

import requests


def elastic_headers_auth(
    api_key: Optional[str], user: Optional[str], password: Optional[str]
) -> Tuple[dict, Optional[tuple]]:
    headers: dict = {}
    auth = None
    if api_key:
        key = api_key
        if ":" in key and " " not in key:
            key = base64.b64encode(key.encode()).decode()
        headers["Authorization"] = f"ApiKey {key}"
    elif user and password:
        auth = (user, password)
    return headers, auth


def fetch_task(host: str, task_id: str, headers: dict, auth: Optional[tuple]) -> Any:
    url = host.rstrip("/") + "/_tasks/" + task_id
    r = requests.get(url, headers=headers or None, auth=auth, timeout=60)
    r.raise_for_status()
    return r.json()


def main():
    import bootstrap_env

    bootstrap_env.load()

    parser = argparse.ArgumentParser(
        description="Poll GET _tasks/<task_id> until the reindex (or other) task finishes."
    )
    parser.add_argument("--task-id", required=True, help="Task id from the reindex API response (without task: prefix).")
    parser.add_argument("--dest-host", default=os.environ.get("DEST_ELASTIC_HOST"), help="Elastic base URL")
    parser.add_argument("--dest-api-key", default=os.environ.get("DEST_ELASTIC_API_KEY"), help="Elastic API key")
    parser.add_argument("--dest-user", default=os.environ.get("DEST_ELASTIC_USER"), help="Elastic user")
    parser.add_argument("--dest-password", default=os.environ.get("DEST_ELASTIC_PASSWORD"), help="Elastic password")
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between polls (default 5).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=86400.0,
        help="Max total seconds to wait (default 86400 = 24h).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print raw JSON each poll.",
    )
    args = parser.parse_args()

    if not args.dest_host:
        parser.error("--dest-host or DEST_ELASTIC_HOST is required")
    if not args.dest_api_key and not (args.dest_user and args.dest_password):
        parser.error("Set --dest-api-key or (--dest-user and --dest-password) for Elastic.")

    task_id = args.task_id
    if task_id.startswith("task:"):
        task_id = task_id[5:]

    headers, auth = elastic_headers_auth(args.dest_api_key, args.dest_user, args.dest_password)

    deadline = time.monotonic() + args.timeout
    last_json = None

    while time.monotonic() < deadline:
        try:
            last_json = fetch_task(args.dest_host, task_id, headers, auth)
        except requests.RequestException as e:
            print(f"Request failed: {e}", file=sys.stderr)
            if hasattr(e, "response") and e.response is not None:
                print(e.response.text[:1000], file=sys.stderr)
            sys.exit(1)

        if args.verbose:
            print(json.dumps(last_json, indent=2))

        completed = last_json.get("completed", False)
        if completed:
            error = last_json.get("error")
            response = last_json.get("response", {})
            fail = response.get("failures") if isinstance(response, dict) else None
            if error:
                print(f"Task finished with error: {json.dumps(error, indent=2)}", file=sys.stderr)
                sys.exit(1)
            if fail:
                print(f"Task finished with failures ({len(fail)}): {json.dumps(fail[:10], indent=2)}", file=sys.stderr)
                sys.exit(1)
            print("Task completed successfully.")
            if isinstance(response, dict) and "total" in response:
                print(f"Total: {response.get('total')} created: {response.get('created')} updated: {response.get('updated')} deleted: {response.get('deleted')}")
            sys.exit(0)

        status = last_json.get("status", {})
        if isinstance(status, dict):
            desc = status.get("description", "")
            total = status.get("total", "?")
            created = status.get("created", "?")
            print(f"Still running… total={total} created={created} {desc[:80]}", flush=True)
        else:
            print("Still running…", flush=True)

        time.sleep(args.interval)

    print(f"Timed out after {args.timeout}s; last state: {json.dumps(last_json, indent=2) if last_json else 'n/a'}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
