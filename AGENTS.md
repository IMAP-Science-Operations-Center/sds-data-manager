# AGENTS.md

This file provides guidance to LLM Agents when working with code in this repository.

## Project

AWS infrastructure (CDK), Dagster orchestration, and API services for NASA's IMAP mission Science Data System. Goal: keep everything mission-agnostic except data-product configuration.

## High-Level Directory Map
- `sds_data_manager/constructs/`: AWS CDK infrastructure definitions (Stacks and Constructs).
- `sds_data_manager/lambda_code/`: Source code for AWS Lambda functions, grouped by domains (`api_lambdas/` for query, download, upload, release, spice, and batch job/logs; `pipeline_lambdas/` for the indexer, packet downloader, monitoring, schema creation, and reprocessing proxy).
- `sds_data_manager/orchestration/`: Dagster orchestration logic, custom behaviors, and YAML configuration files.
- `sds_data_manager/utils/stackbuilder.py`: Composes every CDK stack; `app.py` delegates to it.
- `alembic/`: Database migrations for PostgreSQL schema.
- `scripts/`: Various utility scripts (e.g., `authorization/` for handling API keys).
- `tests/`: Pytest test suite, mocking AWS/Docker locally.

## Commands

```bash
# Install (the dev container's postCreateCommand already does all of this)
poetry install --with layer-database --with layer-spice --with layer-processing --with cdk-install --extras dev --extras test

# Tests (CI runs: pytest --cov --cov-report=xml -m "not network")
poetry run pytest
poetry run pytest tests/orchestration/test_end_to_end.py
poetry run pytest tests/orchestration/test_mag.py::test_name
poetry run pytest -m "not network"      # skip tests needing network

# Lint / format (ruff 0.2.1 pinned in extras; pre-commit pins v0.15.20)
poetry run ruff check --fix .
poetry run ruff format .
poetry run pre-commit run --all-files

# CDK synth — the main "does infrastructure still build" check; CI runs it
poetry run cdk synth
```

## Development Workflow Notes

- **Docker is required** for `tests/orchestration/`: the `postgres_container` fixture spins up a real `postgres:15-alpine` via `testcontainers`. Most unit tests use in-memory SQLite (`tests/conftest.py`) and `moto` mocks, while `tests/integration/` targets live development AWS resources and is marked `network`.
- **Dependency changes require regenerating lambda requirements.** The `pyproject.toml` poetry groups (`layer-database`, `layer-spice`, `layer-processing`, `layer-idex-processing`) are exported to `requirements.txt` files under `lambda_layer/` and `sds_data_manager/lambda_code/`. `pre-commit` does this automatically via its `poetry-export` hooks — run pre-commit after touching dependencies instead of editing those files by hand.
- **Branches**: `main` and `dev` are protected by a `no-commit-to-branch` pre-commit hook. Pushing to `dev` triggers a deploy to the dev AWS account.
- **Ruff exemption**: `alembic/versions/*.py` is exempt from all ruff rules.
- Running alembic requires `DATABASE_URL` in the environment (see `alembic/env.py`).

## Documentation Index

- **[docs/imap-architecture.md](docs/imap-architecture.md)** — data model and system design: data types, the CDK stack composition, the S3/EventBridge/indexer ingest pipeline, the database and Alembic migrations, Dagster's YAML-driven job generation and runtime model, and the APIs. Always read this before doing non-trivial work.

## Hard Rules (MUST FOLLOW)
1. **NO DEPLOYMENT COMMANDS**: NEVER suggest deployment commands like `cdk deploy` as a solution to users unless explicitly asked in the context of a deployment script. Deploys happen via GitHub Actions.
2. **DAGSTER YAML**: DO NOT read the ~6,500 lines of Dagster YAML configurations in `sds_data_manager/orchestration/dependencies/*.yaml` wholesale. Grep for the specific `(data_type, descriptor)` key you need, and only read a file in full when explicitly asked to modify job definitions or inputs.
3. **SCIENCE ALGORITHMS**: The actual science algorithms live in a different repository (`imap-processing`). The batch jobs submitted by Dagster use containers built from that repo. Do not try to implement science processing logic here.
4. **CUSTOM BEHAVIOR OVER SPECIAL CASES**: To change behavior for a single data product, subclass the handler in `sds_data_manager/orchestration/custom_behavior/` and register it — do not special-case inside the generic handler. See [imap-architecture.md](docs/imap-architecture.md).
5. **GENERATED REQUIREMENTS FILES**: Do not hand-edit the `requirements.txt` files under `lambda_layer/` and `sds_data_manager/lambda_code/` — they are exported from `pyproject.toml` by pre-commit hooks (see below).

## Keeping these files in sync

When you learn something new about this repo that future developers need, add it to the relevant file in `docs/`, or make a new file and add it there, instead of expanding this one.