"""Offline checks: CLI scripts start and --help exits successfully."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SCRIPTS = [
    "validate_migration.py",
    "multi_index_reindex.py",
    "poll_reindex_task.py",
]


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_help_exits_zero(script: str) -> None:
    path = ROOT / script
    assert path.is_file(), f"Missing {path}"
    proc = subprocess.run(
        [sys.executable, str(path), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
