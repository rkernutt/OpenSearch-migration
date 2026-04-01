# OpenSearch → Elastic migration kit — common CLI entrypoints for automation.
# Usage: make test | make preflight ARGS="..." | make validate ARGS="..."
# Requires: Python 3.9+ on PATH (override with PYTHON=python3.11).

PYTHON ?= python3

.PHONY: help test lint preflight validate poll-task reindex-gen

help:
	@echo "OpenSearch-migration — make targets for CLI/automation"
	@echo ""
	@echo "  make test              Run pytest (offline-safe; no cluster required)"
	@echo "  make lint              Ruff check/format check + Mypy (dev deps required)"
	@echo "  make preflight ARGS=\"--strict-exit-codes ...\"   preflight.py (see --help)"
	@echo "  make validate ARGS=\"...\"   validate_migration.py"
	@echo "  make poll-task ARGS=\"--task-id ...\"   poll_reindex_task.py"
	@echo "  make reindex-gen ARGS=\"--indices a,b --large\"   multi_index_reindex.py"
	@echo ""
	@echo "Examples:"
	@echo "  make preflight ARGS=\"--strict-exit-codes --source-index logs-2024 --dest-index logs-2024\""
	@echo "  make validate ARGS=\"--strict-exit-codes --output-format json --source-index i --dest-index i\""
	@echo ""
	@echo "Docs: docs/AUTOMATION.md — orchestration: docs/ORCHESTRATION.md"

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy

preflight:
	$(PYTHON) preflight.py $(ARGS)

validate:
	$(PYTHON) validate_migration.py $(ARGS)

poll-task:
	$(PYTHON) poll_reindex_task.py $(ARGS)

reindex-gen:
	$(PYTHON) multi_index_reindex.py $(ARGS)
