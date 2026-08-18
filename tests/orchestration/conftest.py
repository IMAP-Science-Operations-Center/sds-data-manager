"""Unit testing configuration for tests/orchestration."""

import datetime
import json
import os
from unittest.mock import Mock, patch

import boto3
import imap_data_access
import pytest
from dagster import (
    build_sensor_context,
    instance_for_test,
)
from moto import mock_ecr, mock_s3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from testcontainers.postgres import PostgresContainer

from sds_data_manager.lambda_code.SDSCode.api_lambdas import upload_api
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.database.models import (
    Base,
)
from sds_data_manager.orchestration import imap_job
from sds_data_manager.orchestration.imap_dagster import defs

BUCKET_NAME = "test-data-bucket"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch, tmpdir):
    """Set global environment variables."""
    monkeypatch.setenv("S3_BUCKET", BUCKET_NAME)
    # Mock AWS Credentials for moto
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    # Mock the api gateway url
    # This is used in batch_starter.py
    monkeypatch.setenv("IMAP_DATA_ACCESS_URL", "https://test.url")
    monkeypatch.setenv("DATA_DIR", str(tmpdir))
    monkeypatch.setenv("REGION", "testing")
    monkeypatch.setenv(
        "REPROCESSING_SQS_URL",
        "https://sqs.us-west-2.amazonaws.com/test/reprocessing_queue",
    )


@pytest.fixture(autouse=True)
def setup_s3(s3_client):
    """Populate the mocked s3 client with a bucket and a file.

    Each test below will use this fixture by default.
    """
    bucket_name = os.getenv("S3_BUCKET")
    s3_client.create_bucket(
        Bucket=bucket_name,
    )
    result = s3_client.list_buckets()
    assert len(result["Buckets"]) == 1
    assert result["Buckets"][0]["Name"] == bucket_name

    # patch the mocked client into the upload_api module
    # These have to be patched in because they were imported
    # prior to test discovery and would have the default values (None)
    with (
        patch.object(upload_api, "S3_CLIENT", s3_client),
        patch.object(upload_api, "BUCKET_NAME", bucket_name),
    ):
        yield s3_client


@pytest.fixture(autouse=True)
def s3_client():
    """Mock S3 Client, so we don't need network requests."""
    with mock_s3():
        s3_client = boto3.client("s3", region_name="us-east-1")

        # Bucket creation is handled in the setup_s3 fixture.
        yield s3_client


@pytest.fixture(autouse=True)
def ecr_client():
    """Mock ECR client."""
    with mock_ecr():
        ecr_client = boto3.client("ecr", region_name="us-west-2")
        # Create a mock repository for each instrument, and add a mock image to each
        # repository
        for instrument in [
            "swapi",
            "hi",
            "lo",
            "mag",
            "idex",
            "swe",
            "ultra",
            "spacecraft",
            "glows",
        ]:
            ecr_client.create_repository(repositoryName=f"{instrument}-repo")
            ecr_client.put_image(
                registryId="123456789012",
                repositoryName=f"{instrument}-repo",
                imageManifest=json.dumps({}),
                imageManifestMediaType="json",
                imageTag="latest",
                imageDigest=f"sha256:123example{instrument}digest",
            )
        with (
            patch.object(imap_job, "ECR_CLIENT", ecr_client),
        ):
            yield ecr_client


@pytest.fixture(autouse=True)
def batch_client():
    """Fixture to mock BATCH_CLIENT."""
    mock_batch_client = Mock()

    def get_job_definition(jobDefinitionName, status=None):  # noqa: N803
        instrument = jobDefinitionName.split("-")[1]
        return {
            "jobDefinitions": [
                {
                    "revision": 1,
                    "status": "ACTIVE",
                    "containerProperties": {
                        "image": f"123456789012.dkr.ecr.us-west-2.amazonaws.com/"
                        f"{instrument}-repo:latest"
                    },
                }
            ]
        }

    # Mock describe_job_definitions to return a valid job definition
    mock_batch_client.describe_job_definitions.side_effect = get_job_definition

    def return_job_info(
        jobName,  # noqa: N803
        jobQueue,  # noqa: N803
        jobDefinition,  # noqa: N803
        containerOverrides,  # noqa: N803
        retryStrategy,  # noqa: N803
    ):
        return {
            "jobId": "mock-test-job-id-123",
            "jobName": jobName,
            "jobDefinition": jobDefinition,
            "jobQueue": jobQueue,
        }

    # Mock submit_job to safely return a dummy job ID
    mock_batch_client.submit_job.side_effect = return_job_info

    # Mock describe_jobs
    mock_batch_client.describe_jobs.return_value = {
        "jobs": [
            {
                "jobId": "mock-test-job-id-123",
                "status": "SUCCEEDED",
                "stoppedAt": 1480460816500,
                "container": {"logStreamName": "mock/log/stream/123"},
                "jobDefinition": "testDef",
            }
        ]
    }
    mock_logs_client = Mock()
    mock_logs_client.get_log_events.return_value = {"events": []}
    with (
        patch.object(imap_job, "BATCH_CLIENT", mock_batch_client),
        patch.object(imap_job, "LOGS_CLIENT", mock_logs_client),
    ):
        yield mock_batch_client


@pytest.fixture
def mock_upload_request_success():
    """Fixture to mock upload_api and requests.put for successful uploads."""
    with (
        patch.object(imap_job, "upload_api") as mock_upload_api,
        patch("sds_data_manager.orchestration.imap_job.requests") as mock_requests,
    ):
        mock_upload_api.lambda_handler.return_value = {
            "statusCode": 200,
            "body": json.dumps(
                "https://s3.amazonaws.com/bucket/presigned-url?signature=test"
            ),
        }
        mock_requests.put.return_value = Mock(status_code=200)
        yield mock_upload_api, mock_requests


@pytest.fixture(scope="module")
def postgres_container():
    """Spins up an isolated Postgres database via Docker."""
    with PostgresContainer("postgres:15-alpine") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
def mock_db_session(postgres_container):
    """Set up the schema and returns a session for the test to use."""
    with patch.object(db, "Session") as mock_session:
        engine = create_engine(postgres_container)

        Base.metadata.create_all(engine)

        with sessionmaker(bind=engine)() as session:
            # Attach this session to the mocked module's Session call
            mock_session.return_value = session

            # Provide the session to the tests
            yield session

            # Cleanup after the test
            session.rollback()
            session.close()
            # Drop tables to ensure clean state for next test
            Base.metadata.drop_all(engine)


@pytest.fixture
def pointing_table_entries(mock_db_session):
    """Create pointing table entries for repoints 1-10."""
    from sds_data_manager.lambda_code.SDSCode.database.models import PointingTable

    records = []
    for i in range(1, 11):
        records.append(
            PointingTable(
                pointing_id=i,
                pointing_start_utc=datetime.datetime(2026, 1, i, 0, 0, 0),
                pointing_end_utc=datetime.datetime(2026, 1, i, 23, 59, 59),
            )
        )
    mock_db_session.add_all(records)
    mock_db_session.commit()
    return records


def _irrelevant_spice_data():
    """Populate irrelevant columns in DB with dummy data."""
    irrelevant_data = {
        "min_date_datetime": datetime.datetime.now(),
        "max_date_datetime": datetime.datetime.now(),
        "file_intervals_datetime": [["0", "0"]],
        "min_date_sclk": "",
        "max_date_sclk": "",
        "file_intervals_sclk": [["0", "0"]],
        "sclk_kernel": "nothing",
        "lsk_kernel": "nothing",
    }
    return irrelevant_data


def _insert_spice_file(session, filename, intervals, upload_time=0):
    spice_object = imap_data_access.SPICEFilePath(filename)
    version = spice_object.spice_metadata["version"]
    metadata_params = {
        "file_name": filename,
        "file_path": f"imap/spice/{filename}",
        "file_root": "".join(filename.rsplit(version, 1)),
        "kernel_type": spice_object.spice_metadata["type"],
        "version": version,
        "file_intervals_j2000": intervals,
        "min_date_j2000": intervals[0][0],
        "max_date_j2000": intervals[-1][1],
        "ingestion_date": datetime.datetime.now() + datetime.timedelta(upload_time),
    } | _irrelevant_spice_data()
    session.add(models.SPICEFiles(**metadata_params))
    session.commit()


def _insert_spin_file(session, filename, upload_time=0, start_date=None, end_date=None):
    if not start_date:
        start_date = datetime.datetime(2026, 1, 1)
    if not end_date:
        end_date = datetime.datetime(2026, 1, 2)

    spice_object = imap_data_access.SPICEFilePath(filename)
    version = spice_object.spice_metadata["version"]
    metadata_params = {
        "file_path": f"imap/spice/{filename}",
        "version": version,
        "start_date": start_date,
        "end_date": end_date,
        "ingestion_date": datetime.datetime.now() + datetime.timedelta(upload_time),
        "released": True,
    }
    session.add(models.SpinFiles(**metadata_params))
    session.commit()


@pytest.fixture
def insert_test_spice_files(mock_db_session):
    """Put a filepath into the test data."""
    # This file should NOT be loaded, because there is a
    # a newer version of the file
    _insert_spice_file(mock_db_session, "naif0012.tls", [[1, 10000000000000]])

    _insert_spice_file(mock_db_session, "imap_sclk_0189.tsc", [[1, 10000000000000]])


@pytest.fixture
def ephemeral_instance(pointing_table_entries):
    """Provide an isolated, in-memory Dagster instance."""
    with instance_for_test() as instance:
        # Add repoint partitions
        context = build_sensor_context(instance=instance)
        add_repoint_partitions_sensor = defs.get_sensor_def("add_repoint_partitions")
        sensor_result = add_repoint_partitions_sensor(context)

        for request in sensor_result.dynamic_partitions_requests:
            instance.add_dynamic_partitions(
                partitions_def_name=request.partitions_def_name,
                partition_keys=request.partition_keys,
            )

        # Add daily partitions
        context = build_sensor_context(instance=instance)
        add_repoint_partitions_sensor = defs.get_sensor_def("add_daily_partitions")
        sensor_result = add_repoint_partitions_sensor(context)

        for request in sensor_result.dynamic_partitions_requests:
            instance.add_dynamic_partitions(
                partitions_def_name=request.partitions_def_name,
                partition_keys=request.partition_keys,
            )

        yield instance
