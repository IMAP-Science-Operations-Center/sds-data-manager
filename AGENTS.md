# CLAUDE.md

This file provides guidance to LLM Agents when working with code in this repository.

## Project

AWS infrastructure (CDK), Dagster orchestration, and API services for NASA's IMAP mission Science Data System. Goal: keep everything mission-agnostic except data-product configuration.

## Where the guidance lives

The full guidance for this repo lives in two files. **Read both before doing non-trivial work here:**

- **[.github/copilot-instructions.md](.github/copilot-instructions.md)** — hard rules, directory map, and the development workflow: install/test/lint/synth commands, the Docker requirement for `tests/orchestration/`, regenerating lambda `requirements.txt` after dependency changes, and branch protections.
- **[.github/imap-architecture.md](.github/imap-architecture.md)** — data model and system design: data types, the CDK stack composition, the S3/EventBridge/indexer ingest pipeline, the database and Alembic migrations, Dagster's YAML-driven job generation and runtime model, and the APIs.

## Hard Rules (MUST FOLLOW)
1. **NO DEPLOYMENT COMMANDS**: NEVER suggest deployment commands like `cdk deploy` as a solution to users unless explicitly asked in the context of a deployment script. Deploys happen via GitHub Actions.
2. **DAGSTER YAML**: DO NOT read the ~6,500 lines of Dagster YAML configurations in `sds_data_manager/orchestration/dependencies/*.yaml` wholesale. Grep for the specific `(data_type, descriptor)` key you need, and only read a file in full when explicitly asked to modify job definitions or inputs.
3. **SCIENCE ALGORITHMS**: The actual science algorithms live in a different repository (`imap-processing`). The batch jobs submitted by Dagster use containers built from that repo. Do not try to implement science processing logic here.
4. **CUSTOM BEHAVIOR OVER SPECIAL CASES**: To change behavior for a single data product, subclass the handler in `sds_data_manager/orchestration/custom_behavior/` and register it — do not special-case inside the generic handler. See [imap-architecture.md](imap-architecture.md).
5. **GENERATED REQUIREMENTS FILES**: Do not hand-edit the `requirements.txt` files under `lambda_layer/` and `sds_data_manager/lambda_code/` — they are exported from `pyproject.toml` by pre-commit hooks (see below).

## Keeping these files in sync

When you learn something new about this repo that future agents need, add it to the relevant `.github/` file above rather than expanding this one.
