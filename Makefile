# OpenSearch → Elastic migration kit — common CLI entrypoints for automation.
# Usage: make test | make preflight ARGS="..." | make validate ARGS="..."
# Requires: Python 3.9+ on PATH (override with PYTHON=python3.11).

PYTHON ?= python3

.PHONY: help test lint preflight compat-check validate poll-task reindex-gen s3-load s3-extract rfs metadata sanitize migrate shadow-diff replay

help:
	@echo "OpenSearch-migration — make targets for CLI/automation"
	@echo ""
	@echo "  make test              Run pytest (offline-safe; no cluster required)"
	@echo "  make lint              Ruff check/format check + Mypy (dev deps required)"
	@echo "  make preflight ARGS=\"--strict-exit-codes ...\"   preflight.py (see --help)"
	@echo "  make compat-check ARGS=\"--strict-exit-codes ...\"   compat_check.py — version/Lucene/k-NN/codec/mapping report"
	@echo "  make validate ARGS=\"...\"   validate_migration.py"
	@echo "  make poll-task ARGS=\"--task-id ...\"   poll_reindex_task.py"
	@echo "  make reindex-gen ARGS=\"--indices a,b --large\"   multi_index_reindex.py"
	@echo "  make s3-extract ARGS=\"--indices a,b --s3-uri s3://bucket/job/\"   s3_migration/s3_extract.py"
	@echo "  make s3-load ARGS=\"--s3-uri s3://bucket/job/ ...\"   s3_migration/s3_bulk_load.py"
	@echo "  make rfs ARGS=\"--upstream-image ... --snapshot-name ...\"   s3_migration/rfs_runner.py"
	@echo "  make metadata ARGS=\"--include templates,index_templates ...\"   metadata_migration/migrator.py"
	@echo "  make sanitize ARGS=\"--input mapping.json\"   metadata_migration/sanitizer.py"
	@echo "  make migrate ARGS=\"<subcommand> ...\"        umbrella CLI dispatching to all of the above"
	@echo "  make shadow-diff ARGS=\"--queries-file ...\"  shadow_diff.py — query-parity cutover gate"
	@echo "  make replay ARGS=\"--captures ...\"            replay/replayer.py — replay captured proxy traffic"
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

compat-check:
	$(PYTHON) compat_check.py $(ARGS)

validate:
	$(PYTHON) validate_migration.py $(ARGS)

poll-task:
	$(PYTHON) poll_reindex_task.py $(ARGS)

reindex-gen:
	$(PYTHON) multi_index_reindex.py $(ARGS)

s3-extract:
	$(PYTHON) -m s3_migration.s3_extract $(ARGS)

s3-load:
	$(PYTHON) -m s3_migration.s3_bulk_load $(ARGS)

rfs:
	$(PYTHON) -m s3_migration.rfs_runner $(ARGS)

metadata:
	$(PYTHON) -m metadata_migration.migrator $(ARGS)

sanitize:
	$(PYTHON) -m metadata_migration.sanitizer $(ARGS)

migrate:
	$(PYTHON) migrate.py $(ARGS)

shadow-diff:
	$(PYTHON) shadow_diff.py $(ARGS)

replay:
	$(PYTHON) -m replay.replayer $(ARGS)
