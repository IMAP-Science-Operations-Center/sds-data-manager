# Copilot Instructions for SDS Data Manager

Welcome to the SDS Data Manager repository. This repository manages the AWS cloud infrastructure (via AWS CDK), Dagster orchestration, and API services for NASA's IMAP mission science data system.

The goal is to keep everything mission-agnostic except the data-product configuration.

## High-Level Directory Map
- `sds_data_manager/constructs/`: AWS CDK infrastructure definitions (Stacks, Constructs). One construct per file.
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

- **Docker is required** for `tests/orchestration/`: the `postgres_container` fixture spins up a real `postgres:15-alpine` via `testcontainers`. Other test directories use in-memory SQLite (`tests/conftest.py`) and `moto` mocks.
- **Dependency changes require regenerating lambda requirements.** The `pyproject.toml` poetry groups (`layer-database`, `layer-spice`, `layer-processing`, `layer-idex-processing`) are exported to `requirements.txt` files under `lambda_layer/` and `sds_data_manager/lambda_code/`. `pre-commit` does this automatically via its `poetry-export` hooks — run pre-commit after touching dependencies instead of editing those files by hand.
- **Branches**: `main` and `dev` are protected by a `no-commit-to-branch` pre-commit hook. Pushing to `dev` triggers a deploy to the dev AWS account.
- **Ruff exemption**: `alembic/versions/*.py` is exempt from all ruff rules.
- Running alembic requires `DATABASE_URL` in the environment (see `alembic/env.py`).

## Architecture and System Flow
For detailed information on the IMAP data pipeline, S3 event flow, CDK stack composition, the database model, Dagster's YAML-driven job generation, partitioning logic, and API structure, **always refer to [.github/imap-architecture.md](imap-architecture.md)**.
