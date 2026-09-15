# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AWS infrastructure (CDK), Dagster orchestration, and API services for NASA's IMAP mission Science Data System. Goal: keep everything mission-agnostic except data-product configuration.

## Where the guidance lives

To avoid drift between agents, the full guidance for this repo lives in two files that both Claude Code and Copilot read. **Read both before doing non-trivial work here:**

- **[.github/copilot-instructions.md](.github/copilot-instructions.md)** — hard rules, directory map, and the development workflow: install/test/lint/synth commands, the Docker requirement for `tests/orchestration/`, regenerating lambda `requirements.txt` after dependency changes, and branch protections.
- **[.github/imap-architecture.md](.github/imap-architecture.md)** — data model and system design: data types, the CDK stack composition, the S3/EventBridge/indexer ingest pipeline, the database and Alembic migrations, Dagster's YAML-driven job generation and runtime model, and the APIs.

## Hard rules

These are repeated here because they override default behavior; the full set is in [.github/copilot-instructions.md](.github/copilot-instructions.md).

1. **Never suggest deployment commands** (`cdk deploy`, etc.) as a solution unless explicitly asked in the context of a deployment script. Deploys happen via GitHub Actions.
2. **Do not read `sds_data_manager/orchestration/dependencies/*.yaml` wholesale** — ~6,500 lines of generated-ish config. Grep for the specific `(data_type, descriptor)` key you need, and only read in full when explicitly modifying job definitions or inputs.
3. **Science algorithms live in a different repo** (`imap-processing`). Batch jobs here submit containers built from that repo. Do not implement science processing logic in this repository.
4. **To change behavior for one data product**, subclass the handler in `sds_data_manager/orchestration/custom_behavior/` and register it — don't special-case inside the generic handler.
5. **Never hand-edit the generated `requirements.txt` files** under `lambda_layer/` and `sds_data_manager/lambda_code/` — run `pre-commit` to re-export them from `pyproject.toml`.

## Keeping these files in sync

When you learn something new about this repo that future agents need, add it to the relevant `.github/` file above rather than expanding this one.
