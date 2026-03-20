"""
Optional end-to-end check against real clusters.

Set OSS_MIGRATION_INTEGRATION=1 and the env vars below (see docs/TESTING.md).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "SOURCE_OPENSEARCH_HOST",
    "DEST_ELASTIC_HOST",
    "SOURCE_VALIDATION_INDEX",
    "DEST_VALIDATION_INDEX",
)


@pytest.mark.integration
def test_validate_migration_live_pair() -> None:
    if os.environ.get("OSS_MIGRATION_INTEGRATION") != "1":
        pytest.skip("Set OSS_MIGRATION_INTEGRATION=1 to run integration tests")
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        pytest.skip(f"Missing env vars: {missing}")
    if not os.environ.get("DEST_ELASTIC_API_KEY") and not (
        os.environ.get("DEST_ELASTIC_USER") and os.environ.get("DEST_ELASTIC_PASSWORD")
    ):
        pytest.skip("Set DEST_ELASTIC_API_KEY or DEST_ELASTIC_USER+DEST_ELASTIC_PASSWORD")

    sample_mode = os.environ.get("OSS_MIGRATION_SAMPLE_MODE", "head")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "validate_migration.py"),
        "--source-index",
        os.environ["SOURCE_VALIDATION_INDEX"],
        "--dest-index",
        os.environ["DEST_VALIDATION_INDEX"],
        "--check-existence",
        "--sample-size",
        os.environ.get("OSS_MIGRATION_SAMPLE_SIZE", "3"),
        "--sample-mode",
        sample_mode,
    ]
    if sample_mode == "time_stratified":
        tf = os.environ.get("OSS_MIGRATION_TIME_FIELD")
        if not tf:
            pytest.skip("OSS_MIGRATION_TIME_FIELD required for time_stratified")
        cmd.extend(["--time-field", tf])
        cmd.extend(["--time-buckets", os.environ.get("OSS_MIGRATION_TIME_BUCKETS", "8")])
    r = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("OSS_MIGRATION_TIMEOUT", "120")),
    )
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
