# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [1.1.0] - 2026-04-02

### Added

- **`version.py`** — single source of truth for `__version__ = “1.1.0”`; `[project]` table added to `pyproject.toml`.
- **`validate_index_name()`** in `validate_migration.py` — validates index names (lowercase, no leading `-`/`_`/`+`, no special characters) before any HTTP call.
- **`_redact_response_text()`** in `validate_migration.py` — strips `ApiKey`/`Bearer` tokens and long Base64 strings from error response bodies before logging; applied in `preflight.py` too.
- **`_cli_log()` / `--log-format=json`** — structured JSON stderr logging in `validate_migration.py` and `poll_reindex_task.py`; plain-text default unchanged.
- **`--dest-api-key-encoded`** flag (+ `DEST_ELASTIC_API_KEY_ENCODED` env var) in `validate_migration.py` and `poll_reindex_task.py` — skip automatic Base64 encoding when key is already encoded; Base64 heuristic (`”:”` detection) documented.
- **`--mask-credentials`** flag in `multi_index_reindex.py` — replaces `username`/`password` in generated output with `***`; plaintext warning added.
- **`_make_session()`** retry adapter (urllib3 `Retry`, backoff, 429/5xx) in `validate_migration.py`, `preflight.py`, and `poll_reindex_task.py`; all HTTP calls use the session.
- **`VALIDATION_TIMEOUT_SHORT` / `VALIDATION_TIMEOUT_SEARCH`** env vars — configurable request timeouts (default 30 s / 120 s).
- **Proxy hardening** (`Proxy/app.py`): `None` credential guard in `_get_sigv4_auth()` with clear error; `PROXY_VERIFY_TLS` / `PROXY_CA_BUNDLE` env vars for TLS control; `PROXY_DEBUG=1` logs method/path/status/latency without bodies; `/health` endpoint for ALB health checks.
- **`Proxy/opensearch-proxy.service`** — systemd unit file for host-based Gunicorn production deployment.
- **`docs/PRODUCTION_CHECKLIST.md`** — 8-section blocking/non-blocking go/no-go checklist for production cutover.
- **`tests/test_new_utilities.py`** — 20 tests covering `validate_index_name`, `_redact_response_text`, `DestAuth` encoding, `elastic_headers_auth`, and `validate_pair` early exit.
- **GUI tooling** (`gui/`): `eslint.config.js`, `.prettierrc`, `.prettierignore`; Prettier added to `devDependencies`; `format` / `format:check` scripts; `gui/README.md`.
- **`--json-progress`** flag in `poll_reindex_task.py` — emits one JSON line per poll interval (timestamp, status, created, total).

### Changed

- `validate_migration.py`: replaced `assert` statements with explicit `if` guards returning `(False, msg, “validation”)`; `DestAuth.apply()` encoding heuristic clarified; `_sample_doc_ids_time_stratified` reports empty bucket count in output note; `_cli_log` replaces bare `print(..., file=sys.stderr)` for error/warning messages.
- `poll_reindex_task.py`: transient network errors now tolerate up to 5 consecutive failures before aborting (with per-attempt counter) instead of exiting on first failure.
- `preflight.py`: missing-index error messages now include the hostname for clarity.
- `Proxy/requirements.txt`: upper bounds added (`flask<4`, `requests<3`, `boto3<2`, `gunicorn<24`).
- `SECURITY.md`: IAM section split into read-only (validate/preflight) vs. full (reindex/proxy) policies; new “Credential rotation and long-running jobs” section; env var reference table updated.
- `Proxy/README.md`: forwarded-header whitelist documented with rationale; new env vars in config table; production Gunicorn deployment section added.
- `Logstash_input/README.md`: “Handling backpressure and bulk rejections” section with 6 tuning steps and DLQ guidance.
- `RUNBOOK.md`: new section on running Logstash and `validate_migration.py` in parallel, including `refresh_interval` lag and forced-refresh steps.
- `RECOMMENDATIONS.md`: all open items marked complete; implementation map updated.

## [1.0.0] - 2026-04-02

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
