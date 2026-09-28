# IMAP Science Data System Architecture

This repository manages the AWS cloud infrastructure (CDK, Lambdas, Batch, API Gateway) and Dagster orchestration for a science data system for NASA's IMAP mission.

## Infrastructure (CDK)

`app.py` reads `account_name` from the `cdk.json` context (`dev` is the default, plus `prod` and `backup`) and delegates to `sds_data_manager/utils/stackbuilder.py`, which composes every stack:

- **`NetworkingStack`** — VPC and shared networking.
- **`HostedZoneCertificateStack`** — Route 53 hosted zone and ACM certificates.
- **`WebsiteStack`** — the static mission website (deployed to `us-east-1`).
- **`SDCStack`** — the bulk of the system: buckets, database, API gateway, indexer, and batch/processing.
- **`IalirtStack`** — real-time telemetry (I-ALiRT), largely independent of the rest.
- **`DagsterStack`** — the Dagster ECS Fargate deployment.
- **`BackupStack`** — cross-account backups.

Constructs are one-per-file under `sds_data_manager/constructs/`.

`poetry run cdk synth` is the main "does the infrastructure still build" check, and CI runs it. Never suggest `cdk deploy` — deploys happen through GitHub Actions.

**Relevant Files/Directories:**
- `app.py`, `cdk.json`
- `sds_data_manager/utils/stackbuilder.py`
- `sds_data_manager/constructs/`

## Data Types

- **L0 Raw Telemetry**: Arrives broken up by instrument (e.g., `{instrument}_l0_raw`), separated by day (midnight to midnight UTC) or by repoint number.
- **Science Files**: Generated via AWS Batch jobs based on L0 and other files. Identified by instrument, processing level, and a descriptor (e.g., "glows_l1a_hist"). Divided by day, repoint number, or longer intervals (10 day, 30 day, 3/6 month, 1 year). Includes start date, major and minor version numbers.
- **Ancillary Files**: Any format, uploaded by instrument teams, needed for processing.
- **SPICE Files**: Contain geometry info (location, attitude, spacecraft clock, leapseconds). Used primarily for min/max dates and time conversions. Delivered directly by the Mission Operations Center.
- **Spin Files**: Information about the spacecraft's rotation rate and rotation number.
- **Repoint Files**: Define repointing maneuvers adjusting the spacecraft axis. Data taken during the maneuver itself is typically discarded.

**Relevant Files/Directories:**
- `sds_data_manager/lambda_code/SDSCode/spice_utilities.py`
- `sds_data_manager/orchestration/repoint_file.py`
- `sds_data_manager/orchestration/spice.py`
- `sds_data_manager/orchestration/spin.py`

## AWS Pipeline (Ingest & Event Flow)

1. **Ingest**: Files arrive via the upload API or are pulled by a scheduled Lambda function (packet downloader). They are placed in an S3 bucket with strict naming conventions defined in the `imap-data-access` library.
2. **EventBridge & Indexer**: When new files arrive in S3, EventBridge triggers the Indexer Lambda.
3. **Database Insertion**: The Indexer extracts metadata from the file and inserts it into the appropriate database table depending on the data type.

**Relevant Files/Directories:**
- **Packet Downloader Lambda**: `sds_data_manager/lambda_code/SDSCode/pipeline_lambdas/packet_downloader.py`
- **Indexer Lambda**: `sds_data_manager/lambda_code/SDSCode/pipeline_lambdas/indexer.py`
- **Indexer CDK Construct**: `sds_data_manager/constructs/indexer_lambda_construct.py`
- **Database Constructs/Models**: `sds_data_manager/constructs/database_construct.py`, `sds_data_manager/lambda_code/SDSCode/database/`

## Database

PostgreSQL in AWS, modeled with SQLAlchemy in `sds_data_manager/lambda_code/SDSCode/database/models.py`:
`ScienceFiles`, `QuicklookFiles`, `SPICEFiles`, `AncillaryFiles`, `ReleaseFiles`, `SpinFiles`, `RepointFiles`, `PointingTable`, `ProcessingJob`, `IDEXL0Files`, and `Version`.

Schema changes are managed with Alembic; migrations live in `alembic/versions/` and `alembic/env.py` requires `DATABASE_URL` to be set in the environment. `alembic/versions/*.py` is exempt from all ruff rules.

Tests under `tests/orchestration/` run against a real `postgres:15-alpine` container via `testcontainers` (so Docker is required); other test directories use in-memory SQLite (`tests/conftest.py`) with `moto` mocks for AWS.

**Relevant Files/Directories:**
- `sds_data_manager/lambda_code/SDSCode/database/models.py`
- `sds_data_manager/constructs/database_construct.py`
- `alembic/`

## Dagster Orchestration

Dagster is deployed in an ECS Fargate cluster with separate clusters for the Daemon, Web Server, and Read-Only Web Server.

### Everything is generated from YAML config

Jobs and assets are **not** hand-written per data product — they are generated from configuration:

1. `dependencies/imap_{instrument}_dependencies.yaml` declares each job under a `(data_type, descriptor)` key with `partition`, `inputs`, and `outputs`.
2. `dependency.py::DependencyConfigReader` loads all of those files into `{(source, data_type, descriptor): ProcessingJobNode}`.
3. `imap_dagster.py` (the entrypoint that builds `defs`) walks every config key, sends jobs to `JobBuilderRegistry` and unproduced inputs to `FileBuilderRegistry`, then assembles the assets and sensors.
4. The registries return the default handler — `imap_job.py::IMAPJobHandler` or `imap_file.py::IMAPScienceFileHandler` — unless a subclass in `custom_behavior/` registered itself for that key via `@JobBuilderRegistry.register(source, data_type, descriptor)` (or `register_descriptor_pattern`). `imap_dagster.load_all_builders()` imports every `custom_behavior/` module at import time so those decorators fire.

**To change behavior for one product, subclass the handler in `custom_behavior/` and register it — do not special-case inside the generic handler.** Existing examples: `mag.py`, `hi.py`, `idex.py`, `spacecraft.py`, `l3_jobs.py`, `l2_map_jobs.py`.

### Runtime model

- **Kickoff Sensors & Assets**: Each processing job has a "kickoff" sensor that monitors upstream asset materializations/dependency tables and yields a `RunRequest` when enough new data has arrived to attempt a run. Dependencies are complex, so a run is easily triggered but reports an `AssetObservation` (not a failure) if data actually turns out to be missing.
- **Batch Processing**: If sufficient data exists, the asset op submits a job to AWS Batch, writes to the `processing_jobs` table, and monitors the job status, reporting an `AssetObservation` on success/skip or raising `Failure` on job failure. The op never materializes its own output assets.
- **Materialization Sensor**: A single, separate sensor (`science_file_materialization_sensor`, built by `imap_file.build_materialization_sensor`) polls the `science_files` table for newly ingested files and performs runless materializations (`SensorResult(asset_events=...)`) for the matching asset/partition. This decouples "the batch job succeeded" from "the asset got materialized" so a materialization is never lost if a run is interrupted after the Batch job completes but before it would have reported success. This sensor also materializes file-only assets (e.g. raw L0 files) that have no associated Batch job, replacing what used to be one sensor per file node. Handlers opt out by setting `USE_COMMON_SENSOR = False` and supplying their own sensor; IDEX's L0 raw asset is the current exception, self-materializing via its own asset/sensor pair.
- **Partitions**: Assets are partitioned dynamically using custom names with start and end times (daily, repoint, 10-day, 30-day, 3/6-month, 1-year) — see `custom_partitions.py` and `config.py` (`MISSION_START_TIME`, `CadenceDays`). The web UI acts as a dashboard for scientists to monitor product statuses.
- **Storage scope**: Dagster currently *only* orchestrates science files (omitting ancillary and SPICE files due to arbitrary times).

**Relevant Files/Directories:**
- **Dagster Logic & Customs**: `sds_data_manager/orchestration/` (including `imap_dagster.py`, `imap_job.py`, `imap_file.py`, `dependency.py`, `config.py`)
- **Per-product overrides**: `sds_data_manager/orchestration/custom_behavior/`
- **YAML job config**: `sds_data_manager/orchestration/dependencies/imap_{instrument}_dependencies.yaml` (large — grep for the `(data_type, descriptor)` key rather than reading whole files)
- **Custom Partitions**: `sds_data_manager/orchestration/custom_partitions.py`
- **Dagster CDK Construct**: `sds_data_manager/constructs/dagster_construct.py`

## APIs and Data Distribution

We provide APIs to query databases and download specific data products.
- **API Gateway**: Handles incoming requests for file queries, downloads, and uploads.
- **Authentication**: Managed via API keys distributed to users.
- **`imap-data-access`**: An external maintained Python library summarizing API integrations for end users.

Note: There is also a static S3 website for the mission hosted on Route 53, for which this repo provides some underlying infrastructure but no deployable HTML/JS code.

**Relevant Files/Directories:**
- **API Lambdas (Query, Download, Upload)**: `sds_data_manager/lambda_code/SDSCode/api_lambdas/`
- **API Gateway CDK Constructs**: `sds_data_manager/constructs/api_gateway_construct.py`, `sds_data_manager/constructs/sds_api_manager_construct.py`
- **Authorization Scripts**: `scripts/authorization/` (e.g., `lambda_api_key_authorizer.py`, `manage_api_keys.py`)
