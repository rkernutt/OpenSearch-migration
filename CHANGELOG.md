# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Apache-2.0 `LICENSE` and `NOTICE`.
- Dependabot configuration for pip (root + `Proxy/`) and GitHub Actions.
- `pyproject.toml` with Ruff and Mypy settings; `types-requests` for type checking.
- CI: Python **3.9** and **3.11** matrix; **Ruff** (check + format); **Mypy**; **Terraform** `validate` for `iac/terraform/proxy-alb` and `proxy-ecs`.
- `tests/test_strict_exit_codes.py` for `validate_migration.py --strict-exit-codes`.
- `Makefile` **lint** target (Ruff + Mypy).
- Request body size limit on the OpenSearch **Proxy** via `PROXY_MAX_BODY_MB` (Flask `MAX_CONTENT_LENGTH`).
- Upper bounds on runtime dependencies in `requirements.txt` for more predictable upgrades.
- Documentation: this changelog; README **Documentation map**; optional Tines “screenshot / recording” note in `examples/tines/README.md`.

### Changed

- `requirements-dev.txt`: added `ruff`, `mypy`, `types-requests` with upper bounds.
- `validate_migration.validate_pair`: explicit error when basic auth is selected but user/password is missing (defensive; also satisfies type checking).

### Fixed

- `.gitignore`: `.pytest_cache/`, coverage artifacts, `*.egg-info/`.
