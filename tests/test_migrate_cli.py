"""Tests for the umbrella ``migrate`` CLI dispatcher.

We don't exercise every subcommand end-to-end (their own tests do that);
we just verify routing, --help, --version, unknown command handling, and
sys.argv restoration.
"""

from __future__ import annotations

import sys
from typing import List, Optional

import migrate


def test_help_lists_every_subcommand(capsys) -> None:
    rc = migrate.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    for cmd in (
        "preflight",
        "validate",
        "poll-task",
        "reindex-gen",
        "s3-extract",
        "s3-load",
        "rfs",
        "metadata",
        "sanitize",
        "shadow-diff",
        "replay",
    ):
        assert cmd in out


def test_no_args_shows_help_with_zero_exit(capsys) -> None:
    rc = migrate.main([])
    assert rc == 0
    assert "Commands:" in capsys.readouterr().out


def test_version_prints_version(capsys) -> None:
    rc = migrate.main(["--version"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("migrate ")


def test_unknown_command_returns_2(capsys) -> None:
    rc = migrate.main(["does-not-exist"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown command" in err


def test_argv_aware_subcommand_receives_remainder(monkeypatch) -> None:
    """`migrate s3-load --foo bar` must call s3_bulk_load.main(['--foo', 'bar'])."""
    captured: dict = {}

    def fake_main(argv: Optional[List[str]] = None) -> int:
        captured["argv"] = list(argv or [])
        return 0

    import s3_migration.s3_bulk_load as s3_load

    monkeypatch.setattr(s3_load, "main", fake_main)

    rc = migrate.main(["s3-load", "--foo", "bar", "--baz"])
    assert rc == 0
    assert captured["argv"] == ["--foo", "bar", "--baz"]


def test_argv_via_sys_subcommand_sets_sys_argv(monkeypatch) -> None:
    """Old-style scripts like preflight read sys.argv directly. The
    dispatcher should set sys.argv to ``[prog, *rest]`` before calling
    ``main()`` and restore it afterwards.
    """
    captured: dict = {}

    def fake_main() -> int:
        captured["argv"] = list(sys.argv)
        return 0

    import preflight

    monkeypatch.setattr(preflight, "main", fake_main)

    saved_argv = sys.argv[:]
    rc = migrate.main(["preflight", "--source-host", "https://x"])
    assert rc == 0
    assert captured["argv"][0].endswith("preflight")
    assert captured["argv"][1:] == ["--source-host", "https://x"]
    # Restored.
    assert sys.argv == saved_argv


def test_argv_via_sys_translates_systemexit_int(monkeypatch) -> None:
    def fake_main() -> int:
        raise SystemExit(7)

    import preflight

    monkeypatch.setattr(preflight, "main", fake_main)
    rc = migrate.main(["preflight"])
    assert rc == 7


def test_argv_via_sys_translates_systemexit_string(monkeypatch, capsys) -> None:
    def fake_main() -> int:
        raise SystemExit("usage error: missing --foo")

    import preflight

    monkeypatch.setattr(preflight, "main", fake_main)
    rc = migrate.main(["preflight"])
    assert rc == 2
    assert "usage error" in capsys.readouterr().err


def test_subcommand_can_run_help_through_dispatch(capsys) -> None:
    """`migrate sanitize --help` should run sanitizer's argparse --help and
    return 0 (argparse normally raises SystemExit(0))."""
    rc = migrate.main(["sanitize", "--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Sanitize" in out or "usage" in out.lower()
