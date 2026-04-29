#!/usr/bin/env python3
"""Thin wrapper around the upstream OpenSearch Migrations Reindex-from-Snapshot.

The upstream project (https://github.com/opensearch-project/opensearch-migrations)
ships a Java-based **Reindex-from-Snapshot (RFS)** tool that reads ES/OS S3
snapshots, parses Lucene segments, and bulk-indexes into a target cluster.
Re-implementing that in Python is impractical (no maintained pure-Python Lucene
reader); instead, this script invokes the upstream container with the right
flags and validates the result with the existing :mod:`validate_migration`
script.

Usage shape::

    python -m s3_migration.rfs_runner \\
        --upstream-image ghcr.io/your-org/opensearch-migrations:pinned-tag \\
        --snapshot-name my-snap \\
        --s3-repo-uri s3://my-bucket/snapshots/repo \\
        --s3-region us-east-1 \\
        --target-host https://project.es.region.aws.elastic.cloud \\
        --target-api-key "$DEST_ELASTIC_API_KEY" \\
        --target-type ELASTICSEARCH_SERVERLESS \\
        --source-version OpenSearch_2_13 \\
        --indices-validate "logs-2024,metrics-2024" \\
        --strict-exit-codes

This module deliberately does **not** ship a default ``--upstream-image``: pin
it explicitly per environment (and per upgrade) so RFS behaviour is
reproducible. See ``docs/RFS.md`` for guidance on choosing and pinning images.

Exit codes (with ``--strict-exit-codes``):
  0  RFS completed and (if requested) validation succeeded
  2  configuration error (missing flags, no docker available, etc.)
  3  RFS process failed or validation transport failure
  4  RFS completed but validation reported count mismatches
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import bootstrap_env  # noqa: E402

bootstrap_env.load()

import validate_migration as _vm  # noqa: E402
from validate_migration import _cli_log  # noqa: E402

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_RFS_FAILED = 3
EXIT_VALIDATION_FAILED = 4

# Allowed values from upstream's --target-type. Keep this list narrow; serverless
# is the headline reason for using this wrapper.
_TARGET_TYPES = ("ELASTICSEARCH", "ELASTICSEARCH_SERVERLESS", "OPENSEARCH")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Run upstream OpenSearch Migrations Reindex-from-Snapshot in a "
            "container and validate the result."
        ),
    )

    # --- container / runtime ---
    p.add_argument(
        "--upstream-image",
        default=os.environ.get("RFS_UPSTREAM_IMAGE"),
        help=(
            "Container image tag for the upstream RFS tool "
            "(env: RFS_UPSTREAM_IMAGE). REQUIRED — pin this per environment."
        ),
    )
    p.add_argument(
        "--container-cmd",
        default=os.environ.get("RFS_CONTAINER_CMD", "docker"),
        help="Container runtime binary (default 'docker'; 'podman' also works).",
    )
    p.add_argument(
        "--gradle-task",
        default=os.environ.get("RFS_GRADLE_TASK", "DocumentsFromSnapshotMigration:run"),
        help=(
            "Gradle task to invoke inside the upstream image "
            "(default 'DocumentsFromSnapshotMigration:run')."
        ),
    )
    p.add_argument(
        "--container-arg",
        action="append",
        default=[],
        help=(
            "Extra args passed to the container runtime *before* the image "
            "(e.g. '--network=host'). Repeatable."
        ),
    )

    # --- RFS flags (mirror upstream) ---
    p.add_argument("--snapshot-name", required=True, help="Snapshot name in the S3 repo.")
    p.add_argument(
        "--s3-repo-uri",
        required=True,
        help="s3://bucket/path of the snapshot repository.",
    )
    p.add_argument("--s3-region", default=os.environ.get("AWS_REGION", "us-east-1"))
    p.add_argument(
        "--s3-local-dir",
        default="/tmp/s3_files",
        help="Container path for downloaded snapshot files.",
    )
    p.add_argument(
        "--lucene-dir",
        default="/tmp/lucene_files",
        help="Container path for unpacked Lucene segments.",
    )
    p.add_argument(
        "--source-version",
        required=True,
        help="Upstream --source-version, e.g. OpenSearch_2_13 or Elasticsearch_7_10.",
    )
    p.add_argument("--target-host", required=True)
    p.add_argument(
        "--target-type",
        choices=_TARGET_TYPES,
        default="ELASTICSEARCH_SERVERLESS",
    )
    p.add_argument("--target-api-key", default=os.environ.get("DEST_ELASTIC_API_KEY"))
    p.add_argument("--target-username", default=os.environ.get("DEST_ELASTIC_USER"))
    p.add_argument("--target-password", default=os.environ.get("DEST_ELASTIC_PASSWORD"))

    # --- post-run validation (uses the existing validate_migration.py) ---
    p.add_argument(
        "--indices-validate",
        default=None,
        help=(
            "Optional comma-separated list of indices to validate via "
            "validate_migration.py after RFS finishes."
        ),
    )
    p.add_argument(
        "--validate-sample-size",
        type=int,
        default=0,
        help="Sample-size for the post-run validate step (0 = counts only).",
    )

    # --- ergonomics ---
    p.add_argument("--dry-run", action="store_true", help="Print the docker command and exit.")
    p.add_argument("--log-format", choices=("text", "json"), default="text")
    p.add_argument("--strict-exit-codes", action="store_true")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Pure command builder (testable)
# ---------------------------------------------------------------------------


def build_docker_args(args: argparse.Namespace) -> List[str]:
    """Construct the full container command line.

    The shape is::

        <container-cmd> run --rm <container-args...> <image>
            ./gradlew <gradle-task> --args="<rfs-args...>"

    All AWS / target-host / target-credential values are passed through as
    container env vars (`-e KEY=VALUE`) so they don't end up on the command
    line. The RFS-side flags carry only non-secret arguments.
    """
    if not args.upstream_image:
        raise ValueError(
            "--upstream-image (or RFS_UPSTREAM_IMAGE) is required; "
            "pin a specific tag per environment."
        )

    cmd: List[str] = [args.container_cmd, "run", "--rm"]
    cmd.extend(args.container_arg or [])

    # Forward AWS credentials from the caller's environment so the container
    # can read the S3 snapshot repo. Falls back to the AWS provider chain
    # inside the container (e.g. EC2/ECS task role) when these are unset.
    for env_key in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
    ):
        cmd.extend(["-e", env_key])

    # Target credentials as env vars (avoid leaking on the cmd line).
    if args.target_api_key:
        cmd.extend(["-e", f"TARGET_API_KEY={args.target_api_key}"])
    if args.target_username:
        cmd.extend(["-e", f"TARGET_USERNAME={args.target_username}"])
    if args.target_password:
        cmd.extend(["-e", f"TARGET_PASSWORD={args.target_password}"])

    cmd.append(args.upstream_image)

    rfs_args: List[str] = [
        "--snapshot-name",
        args.snapshot_name,
        "--s3-repo-uri",
        args.s3_repo_uri,
        "--s3-region",
        args.s3_region,
        "--s3-local-dir",
        args.s3_local_dir,
        "--lucene-dir",
        args.lucene_dir,
        "--source-version",
        args.source_version,
        "--target-host",
        args.target_host,
        "--target-type",
        args.target_type,
    ]
    if args.target_api_key:
        rfs_args.extend(["--target-api-key", "$TARGET_API_KEY"])
    elif args.target_username and args.target_password:
        rfs_args.extend(["--target-username", "$TARGET_USERNAME"])
        rfs_args.extend(["--target-password", "$TARGET_PASSWORD"])

    cmd.extend(
        [
            "./gradlew",
            args.gradle_task,
            f"--args={_quote_rfs_args(rfs_args)}",
        ]
    )
    return cmd


def _quote_rfs_args(rfs_args: List[str]) -> str:
    """Join RFS args into a single Gradle ``--args=`` string.

    Values that contain whitespace or special characters get double-quoted.
    """
    out: List[str] = []
    for tok in rfs_args:
        if any(c in tok for c in (" ", "\t", '"', "'")):
            escaped = tok.replace('"', '\\"')
            out.append(f'"{escaped}"')
        else:
            out.append(tok)
    return " ".join(out)


# ---------------------------------------------------------------------------
# Run the command
# ---------------------------------------------------------------------------


def _stream_subprocess(cmd: List[str]) -> int:
    """Run *cmd*, stream stdout/stderr lines through `_cli_log`. Return rc."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        _cli_log("info", line.rstrip("\n"), source="rfs")
    proc.wait()
    return int(proc.returncode)


def _run_validation(
    indices: List[str],
    sample_size: int,
    target_host: str,
    api_key: Optional[str],
    user: Optional[str],
    password: Optional[str],
) -> int:
    """Invoke validate_migration.py via subprocess. Returns its exit code."""
    cmd = [
        sys.executable,
        str(_repo_root / "validate_migration.py"),
        "--indices",
        ",".join(indices),
        "--strict-exit-codes",
        "--check-existence",
        "--output-format",
        "json",
        "--dest-host",
        target_host,
    ]
    if sample_size > 0:
        cmd.extend(["--sample-size", str(sample_size)])
    if api_key:
        cmd.extend(["--dest-api-key", api_key])
    if user:
        cmd.extend(["--dest-user", user])
    if password:
        cmd.extend(["--dest-password", password])
    completed = subprocess.run(cmd, check=False)
    return int(completed.returncode)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    _vm._LOG_FORMAT_JSON = args.log_format == "json"

    if shutil.which(args.container_cmd) is None and not args.dry_run:
        _cli_log("error", f"container runtime not found on PATH: {args.container_cmd!r}")
        return EXIT_CONFIG if args.strict_exit_codes else 1
    if not args.target_api_key and not (args.target_username and args.target_password):
        _cli_log("error", "set --target-api-key or both --target-username and --target-password")
        return EXIT_CONFIG if args.strict_exit_codes else 1

    try:
        cmd = build_docker_args(args)
    except ValueError as e:
        _cli_log("error", str(e))
        return EXIT_CONFIG if args.strict_exit_codes else 1

    # Redact target credentials before logging the command.
    safe_cmd: List[str] = []
    for tok in cmd:
        if tok.startswith("TARGET_API_KEY=") or tok.startswith("TARGET_PASSWORD="):
            key, _ = tok.split("=", 1)
            safe_cmd.append(f"{key}=***")
        else:
            safe_cmd.append(tok)

    if args.dry_run:
        _cli_log("info", "[dry-run] would run RFS", cmd=" ".join(safe_cmd))
        return EXIT_OK

    _cli_log("info", "starting RFS", cmd=" ".join(safe_cmd))
    started = time.monotonic()
    rc = _stream_subprocess(cmd)
    elapsed = round(time.monotonic() - started, 1)
    if rc != 0:
        _cli_log("error", f"RFS exited with code {rc}", elapsed_seconds=elapsed)
        return EXIT_RFS_FAILED if args.strict_exit_codes else 1
    _cli_log("info", "RFS completed", elapsed_seconds=elapsed)

    if args.indices_validate:
        indices = [s.strip() for s in args.indices_validate.split(",") if s.strip()]
        if indices:
            _cli_log("info", "running post-RFS validation", indices=indices)
            v_rc = _run_validation(
                indices,
                args.validate_sample_size,
                args.target_host,
                args.target_api_key,
                args.target_username,
                args.target_password,
            )
            if v_rc != 0:
                _cli_log("error", f"validation exited with code {v_rc}")
                # Validation strict codes: 1 validation, 3 transport.
                if v_rc == 3:
                    return EXIT_RFS_FAILED if args.strict_exit_codes else 1
                return EXIT_VALIDATION_FAILED if args.strict_exit_codes else 1

    summary = {
        "ok": True,
        "rfs_elapsed_seconds": elapsed,
        "validated_indices": args.indices_validate,
    }
    if args.log_format == "json":
        print(json.dumps(summary))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
