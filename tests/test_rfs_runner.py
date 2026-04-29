"""Unit tests for the pure command-builder in `s3_migration.rfs_runner`.

The wrapper itself shells out to a container runtime, so the integration is
covered by `docs/RFS.md` rather than an automated test. Here we just exercise
``build_docker_args`` and the credential redaction logic.
"""

from __future__ import annotations

import argparse

import pytest

from s3_migration import rfs_runner


def _ns(**overrides) -> argparse.Namespace:
    base = dict(
        upstream_image="ghcr.io/example/opensearch-migrations:pinned",
        container_cmd="docker",
        gradle_task="DocumentsFromSnapshotMigration:run",
        container_arg=[],
        snapshot_name="my-snap",
        s3_repo_uri="s3://my-bucket/snapshots/repo",
        s3_region="us-east-1",
        s3_local_dir="/tmp/s3_files",
        lucene_dir="/tmp/lucene_files",
        source_version="OpenSearch_2_13",
        target_host="https://example.found.io",
        target_type="ELASTICSEARCH_SERVERLESS",
        target_api_key="id:secret",
        target_username=None,
        target_password=None,
        indices_validate=None,
        validate_sample_size=0,
        dry_run=False,
        log_format="text",
        strict_exit_codes=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_build_docker_args_basic_shape() -> None:
    cmd = rfs_runner.build_docker_args(_ns())
    assert cmd[0:3] == ["docker", "run", "--rm"]
    # AWS env passthrough comes before the image.
    assert "AWS_ACCESS_KEY_ID" in cmd
    assert "AWS_REGION" in cmd
    # Image is followed by the gradle invocation.
    image_idx = cmd.index("ghcr.io/example/opensearch-migrations:pinned")
    assert cmd[image_idx + 1] == "./gradlew"
    assert cmd[image_idx + 2] == "DocumentsFromSnapshotMigration:run"
    assert cmd[image_idx + 3].startswith("--args=")


def test_build_docker_args_passes_rfs_flags_through() -> None:
    cmd = rfs_runner.build_docker_args(_ns())
    args_blob = next(t for t in cmd if t.startswith("--args="))
    assert "--snapshot-name my-snap" in args_blob
    assert "--s3-repo-uri s3://my-bucket/snapshots/repo" in args_blob
    assert "--target-type ELASTICSEARCH_SERVERLESS" in args_blob
    assert "--source-version OpenSearch_2_13" in args_blob
    # Secret reference is by env-var indirection, not the literal value.
    assert "$TARGET_API_KEY" in args_blob
    assert "id:secret" not in args_blob


def test_build_docker_args_basic_auth() -> None:
    cmd = rfs_runner.build_docker_args(
        _ns(target_api_key=None, target_username="elastic", target_password="hunter2")
    )
    args_blob = next(t for t in cmd if t.startswith("--args="))
    assert "$TARGET_USERNAME" in args_blob
    assert "$TARGET_PASSWORD" in args_blob
    assert "hunter2" not in args_blob
    assert "--target-api-key" not in args_blob


def test_build_docker_args_extra_container_args() -> None:
    cmd = rfs_runner.build_docker_args(_ns(container_arg=["--network=host", "--cpus=2"]))
    # Extras land between `run --rm` and any other flags.
    assert cmd[3] == "--network=host"
    assert cmd[4] == "--cpus=2"


def test_build_docker_args_requires_image() -> None:
    with pytest.raises(ValueError, match="upstream-image"):
        rfs_runner.build_docker_args(_ns(upstream_image=None))


def test_build_docker_args_passes_credentials_as_env_vars() -> None:
    cmd = rfs_runner.build_docker_args(_ns())
    # The "-e KEY=value" pairs should carry the secret values, not the cmdline.
    secret_envs = [tok for tok in cmd if tok.startswith("TARGET_API_KEY=")]
    assert secret_envs == ["TARGET_API_KEY=id:secret"]


def test_quote_rfs_args_handles_whitespace() -> None:
    out = rfs_runner._quote_rfs_args(["--query", "needs spaces", "--n", "1"])
    assert '"needs spaces"' in out
    assert "--n 1" in out
