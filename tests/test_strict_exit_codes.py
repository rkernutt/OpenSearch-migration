"""Exit code behavior for validate_migration (strict vs default)."""

from __future__ import annotations

import sys

import pytest

import validate_migration as vm


def _argv(*args: str) -> None:
    sys.argv = ["validate_migration.py", *args]


def test_strict_validation_failure_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def _pair(*_a, **_k):
        return False, "count mismatch", "validation"

    monkeypatch.setattr(vm, "validate_pair", _pair)
    _argv(
        "--strict-exit-codes",
        "--source-host",
        "https://os.example",
        "--dest-host",
        "https://es.example",
        "--source-user",
        "u",
        "--source-password",
        "p",
        "--dest-api-key",
        "k",
        "--source-index",
        "idx",
        "--dest-index",
        "idx",
    )
    with pytest.raises(SystemExit) as exc:
        vm.main()
    assert exc.value.code == 1


def test_strict_transport_failure_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    def _pair(*_a, **_k):
        return False, "connection reset", "transport"

    monkeypatch.setattr(vm, "validate_pair", _pair)
    _argv(
        "--strict-exit-codes",
        "--source-host",
        "https://os.example",
        "--dest-host",
        "https://es.example",
        "--source-user",
        "u",
        "--source-password",
        "p",
        "--dest-api-key",
        "k",
        "--source-index",
        "idx",
        "--dest-index",
        "idx",
    )
    with pytest.raises(SystemExit) as exc:
        vm.main()
    assert exc.value.code == 3


def test_strict_prefers_transport_when_mixed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []

    def _pair(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            return False, "bad", "validation"
        return False, "timeout", "transport"

    monkeypatch.setattr(vm, "validate_pair", _pair)
    _argv(
        "--strict-exit-codes",
        "--source-host",
        "https://os.example",
        "--dest-host",
        "https://es.example",
        "--source-user",
        "u",
        "--source-password",
        "p",
        "--dest-api-key",
        "k",
        "--indices",
        "a,b",
    )
    with pytest.raises(SystemExit) as exc:
        vm.main()
    assert exc.value.code == 3


def test_default_mode_transport_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def _pair(*_a, **_k):
        return False, "timeout", "transport"

    monkeypatch.setattr(vm, "validate_pair", _pair)
    _argv(
        "--source-host",
        "https://os.example",
        "--dest-host",
        "https://es.example",
        "--source-user",
        "u",
        "--source-password",
        "p",
        "--dest-api-key",
        "k",
        "--source-index",
        "idx",
        "--dest-index",
        "idx",
    )
    with pytest.raises(SystemExit) as exc:
        vm.main()
    assert exc.value.code == 1
