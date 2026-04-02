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

import datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


_LOG_FORMAT_JSON = False


def _cli_log(level: str, message: str, **extra) -> None:
    if _LOG_FORMAT_JSON:
        record = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "level": level,
            "message": message,
        }
        record.update(extra)
        print(json.dumps(record), file=sys.stderr)
    else:
        suffix = "".join(f" {k}={v}" for k, v in extra.items())
        print(f"{level.upper()}: {message}{suffix}", file=sys.stderr)


def _make_session() -> requests.Session:
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods={"GET"},
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_SESSION = _make_session()


def elastic_headers_auth(
    api_key: Optional[str],
    user: Optional[str],
    password: Optional[str],
    api_key_encoded: bool = False,
) -> Tuple[dict, Optional[tuple]]:
    headers: dict = {}
    auth = None
    if api_key:
        key = api_key
        if not api_key_encoded and ":" in key:
            # Raw Elastic API keys are id:secret; Base64 alphabet never contains ":"
            key = base64.b64encode(key.encode()).decode()
        headers["Authorization"] = f"ApiKey {key}"
    elif user and password:
        auth = (user, password)
    return headers, auth


def fetch_task(host: str, task_id: str, headers: dict, auth: Optional[tuple]) -> Any:
    url = host.rstrip("/") + "/_tasks/" + task_id
    r = _SESSION.get(url, headers=headers or None, auth=auth, timeout=60)
    r.raise_for_status()
    return r.json()


def main():
    import bootstrap_env

    bootstrap_env.load()

    parser = argparse.ArgumentParser(
        description="Poll GET _tasks/<task_id> until the reindex (or other) task finishes."
    )
    parser.add_argument(
        "--task-id",
        required=True,
        help="Task id from the reindex API response (without task: prefix).",
    )
    parser.add_argument(
        "--dest-host", default=os.environ.get("DEST_ELASTIC_HOST"), help="Elastic base URL"
    )
    parser.add_argument(
        "--dest-api-key", default=os.environ.get("DEST_ELASTIC_API_KEY"), help="Elastic API key"
    )
    parser.add_argument(
        "--dest-api-key-encoded",
        action="store_true",
        default=os.environ.get("DEST_ELASTIC_API_KEY_ENCODED", "").lower() in ("1", "true", "yes"),
        help="Indicate that --dest-api-key is already Base64-encoded (skip automatic encoding).",
    )
    parser.add_argument(
        "--dest-user", default=os.environ.get("DEST_ELASTIC_USER"), help="Elastic user"
    )
    parser.add_argument(
        "--dest-password", default=os.environ.get("DEST_ELASTIC_PASSWORD"), help="Elastic password"
    )
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
    parser.add_argument(
        "--json-progress",
        action="store_true",
        help="Emit one JSON line per poll with timestamp, status, created, and total (useful for CI/dashboards).",
    )
    parser.add_argument(
        "--log-format",
        choices=("text", "json"),
        default="text",
        help="Stderr log format: text (default) or json (one JSON object per line).",
    )
    parser.add_argument(
        "--strict-exit-codes",
        action="store_true",
        help="0=success, 1=task/reindex failure, 2=timeout or argparse, 3=HTTP/network error on poll.",
    )
    args = parser.parse_args()

    if not args.dest_host:
        parser.error("--dest-host or DEST_ELASTIC_HOST is required")
    if not args.dest_api_key and not (args.dest_user and args.dest_password):
        parser.error("Set --dest-api-key or (--dest-user and --dest-password) for Elastic.")

    task_id = args.task_id
    if task_id.startswith("task:"):
        task_id = task_id[5:]

    global _LOG_FORMAT_JSON
    _LOG_FORMAT_JSON = args.log_format == "json"

    headers, auth = elastic_headers_auth(
        args.dest_api_key, args.dest_user, args.dest_password, args.dest_api_key_encoded
    )

    deadline = time.monotonic() + args.timeout
    last_json = None
    consecutive_errors = 0
    _MAX_CONSECUTIVE_ERRORS = 5

    while time.monotonic() < deadline:
        try:
            last_json = fetch_task(args.dest_host, task_id, headers, auth)
            consecutive_errors = 0
        except requests.RequestException as e:
            consecutive_errors += 1
            msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                msg += f" — {e.response.text[:500]}"
            _cli_log("warn", f"Request failed ({consecutive_errors}/{_MAX_CONSECUTIVE_ERRORS}): {msg}")
            if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                _cli_log("error", f"Aborting after {_MAX_CONSECUTIVE_ERRORS} consecutive request failures.")
                sys.exit(3 if args.strict_exit_codes else 1)
            time.sleep(args.interval)
            continue

        if args.verbose:
            print(json.dumps(last_json, indent=2))

        completed = last_json.get("completed", False)
        status = last_json.get("status", {})
        _total = status.get("total", "?") if isinstance(status, dict) else "?"
        _created = status.get("created", "?") if isinstance(status, dict) else "?"

        if args.json_progress:
            import datetime
            progress = {
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "task_id": task_id,
                "status": "completed" if completed else "running",
                "total": _total,
                "created": _created,
            }
            print(json.dumps(progress), flush=True)

        if completed:
            error = last_json.get("error")
            response = last_json.get("response", {})
            fail = response.get("failures") if isinstance(response, dict) else None
            if error:
                print(f"Task finished with error: {json.dumps(error, indent=2)}", file=sys.stderr)
                sys.exit(1)
            if fail:
                print(
                    f"Task finished with failures ({len(fail)}): {json.dumps(fail[:10], indent=2)}",
                    file=sys.stderr,
                )
                sys.exit(1)
            print("Task completed successfully.")
            if isinstance(response, dict) and "total" in response:
                print(
                    f"Total: {response.get('total')} created: {response.get('created')} "
                    f"updated: {response.get('updated')} deleted: {response.get('deleted')}"
                )
            sys.exit(0)

        if not args.json_progress:
            if isinstance(status, dict):
                desc = status.get("description", "")
                print(f"Still running… total={_total} created={_created} {desc[:80]}", flush=True)
            else:
                print("Still running…", flush=True)

        time.sleep(args.interval)

    print(
        f"Timed out after {args.timeout}s; last state: {json.dumps(last_json, indent=2) if last_json else 'n/a'}",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
