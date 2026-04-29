#!/usr/bin/env python3
"""Umbrella CLI for the OpenSearch-to-Elastic migration toolkit.

Dispatches to the individual scripts so operators have one binary instead
of nine. Designed to be invoked either directly (``python migrate.py
<cmd> ...``) or via the ``migrate`` console-script entry point declared in
``pyproject.toml``.

Each subcommand runs the same module that ``make <target>`` would run, so
behaviour is identical to the standalone scripts. Use ``migrate <cmd>
--help`` to see the flags supported by each subcommand.
"""

from __future__ import annotations

import dataclasses
import importlib
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Make the repository root importable when this file is run from anywhere
# (pip install adds it to sys.path automatically; this handles direct
# script invocation).
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Subcommand registry
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Subcommand:
    name: str
    description: str
    module: str
    style: str  # "argv-aware" (main(argv)) or "argv-via-sys" (main() reads sys.argv)


_COMMANDS: List[Subcommand] = [
    Subcommand(
        "preflight",
        "Source/destination connectivity + auth + count parity sanity checks.",
        "preflight",
        "argv-via-sys",
    ),
    Subcommand(
        "validate",
        "Post-load count + sample reconciliation (PASS/FAIL exit codes).",
        "validate_migration",
        "argv-via-sys",
    ),
    Subcommand(
        "poll-task",
        "Poll a long-running OpenSearch _reindex task to completion.",
        "poll_reindex_task",
        "argv-via-sys",
    ),
    Subcommand(
        "reindex-gen",
        "Generate a remote-reindex POST body for one or more indices.",
        "multi_index_reindex",
        "argv-via-sys",
    ),
    Subcommand(
        "s3-extract",
        "Extract OpenSearch indices to gzipped NDJSON in S3 (Path D).",
        "s3_migration.s3_extract",
        "argv-aware",
    ),
    Subcommand(
        "s3-load",
        "Load gzipped NDJSON from S3 into Elasticsearch via _bulk (Path D).",
        "s3_migration.s3_bulk_load",
        "argv-aware",
    ),
    Subcommand(
        "rfs",
        "Run the upstream Reindex-from-Snapshot container (Path E).",
        "s3_migration.rfs_runner",
        "argv-aware",
    ),
    Subcommand(
        "metadata",
        "Migrate templates / component templates / ingest pipelines with sanitization.",
        "metadata_migration.migrator",
        "argv-aware",
    ),
    Subcommand(
        "sanitize",
        "Sanitize an index settings/mapping JSON for the destination (CLI only).",
        "metadata_migration.sanitizer",
        "argv-aware",
    ),
    Subcommand(
        "shadow-diff",
        "Query-parity cutover gate: replay queries against source + dest and report drift.",
        "shadow_diff",
        "argv-aware",
    ),
    Subcommand(
        "replay",
        "Replay captured proxy traffic against the destination (Path F).",
        "replay.replayer",
        "argv-aware",
    ),
]

_BY_NAME: Dict[str, Subcommand] = {c.name: c for c in _COMMANDS}


# ---------------------------------------------------------------------------
# Help formatting
# ---------------------------------------------------------------------------


_USAGE = """\
Usage: migrate <command> [options...]

Commands:
{command_lines}

Run 'migrate <command> --help' for the flags accepted by each subcommand.
Run 'migrate --version' to print the toolkit version.
""".rstrip()


def _format_help() -> str:
    width = max(len(c.name) for c in _COMMANDS)
    lines = [f"  {c.name:<{width}}  {c.description}" for c in _COMMANDS]
    return _USAGE.format(command_lines="\n".join(lines))


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _run_argv_aware(module_name: str, argv: List[str]) -> int:
    module = importlib.import_module(module_name)
    main: Callable[[Optional[List[str]]], int] = module.main
    try:
        rc = main(argv)
    except SystemExit as exc:
        # argparse calls sys.exit(0) on `--help`; don't let that bubble up.
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        sys.stderr.write(str(code) + "\n")
        return 2
    return rc if isinstance(rc, int) else 0


def _run_argv_via_sys(module_name: str, argv: List[str], prog: str) -> int:
    """Run a module whose ``main()`` reads ``sys.argv`` directly.

    Replaces ``sys.argv`` for the duration of the call and translates any
    ``SystemExit`` raised by argparse / the script into a return code.
    """
    module = importlib.import_module(module_name)
    main: Callable[[], object] = module.main

    saved_argv = sys.argv
    sys.argv = [prog] + argv
    try:
        rv = main()
        return rv if isinstance(rv, int) else 0
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        # Argparse passes a string when emitting "argument X: expected one argument".
        sys.stderr.write(str(code) + "\n")
        return 2
    finally:
        sys.argv = saved_argv


def _read_version() -> str:
    """Best-effort read of the version from pyproject.toml."""
    pyproject = _REPO_ROOT / "pyproject.toml"
    if not pyproject.exists():
        return "unknown"
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version"):
            try:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
            except IndexError:
                return "unknown"
    return "unknown"


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(_format_help() + "\n")
        return 0
    if argv[0] in ("-V", "--version", "version"):
        sys.stdout.write(f"migrate {_read_version()}\n")
        return 0

    cmd_name, rest = argv[0], argv[1:]
    spec = _BY_NAME.get(cmd_name)
    if spec is None:
        sys.stderr.write(f"migrate: unknown command '{cmd_name}'\n\n")
        sys.stderr.write(_format_help() + "\n")
        return 2

    # Make sure DOTENV / bootstrap is loaded once per process. The
    # individual modules call this themselves, but doing it at the top level
    # keeps log output predictable.
    try:
        bootstrap_env = importlib.import_module("bootstrap_env")
        bootstrap_env.load()
    except Exception:
        # bootstrap_env is best-effort — never block a CLI run on it.
        pass

    prog = f"migrate {cmd_name}"
    if spec.style == "argv-aware":
        return _run_argv_aware(spec.module, rest)
    return _run_argv_via_sys(spec.module, rest, prog)


if __name__ == "__main__":
    raise SystemExit(main())
