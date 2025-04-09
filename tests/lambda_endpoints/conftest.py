"""Setup testing environment to test lambda handler code."""

from datetime import datetime
from typing import Optional
from unittest.mock import patch

import boto3
import pytest
from moto import mock_events, mock_s3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database.models import (
    AncillaryFiles,
    Base,
    ScienceFiles,
)

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
    monkeypatch.setenv("EFS_SPICE_MOUNT_PATH", str(tmpdir))


@pytest.fixture(scope="module")
def ancillary_file():
    """Path to a valid ancillary file."""
    return "imap/ancillary/swe/imap_swe_test-ancillary-description_20100101_v000.cdf"


@pytest.fixture(scope="module")
def science_file():
    """Path to a valid science file."""
    return "imap/swe/l1a/2010/01/imap_swe_l1a_test-description_20100101_v000.cdf"


@pytest.fixture(scope="module")
def spice_file():
    """Path to a valid spice file."""
    return "spice/ck/imap_2025_032_2025_034_003.ah.bc"


@pytest.fixture(scope="module")
def invalid_file():
    """Path for an invalid file."""
    return (
        "imap/swe/l1a/2010/01/imap_swe_l1a_test-description_"
        "second-description_20100101_v000.cdf"
    )


@pytest.fixture(autouse=True, scope="module")
def s3_client():
    """Mock S3 Client, so we don't need network requests."""
    with mock_s3():
        s3_client = boto3.client("s3", region_name="us-east-1")

        s3_client.create_bucket(
            Bucket=BUCKET_NAME,
        )

        yield s3_client


@pytest.fixture()
def events_client():
    """Mock EventBridge client."""
    with mock_events():
        yield boto3.client("events", region_name="us-west-2")


# Check if `psycopg` and PostgreSQL are both available and compatible.
POSTGRES_AVAILABLE = False
# TODO: fix this to work with postgres locally


# NOTE: The default scope is function, so each test function will
#       get a new database session and start fresh each time.
@pytest.fixture()
def session():
    """Create a test postgres database engine."""
    with patch.object(db, "Session") as mock_session:
        connection = "sqlite:///:memory:"
        engine = create_engine(connection)

        # Create the tables and session
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


def create_dependency_api_event(
    source: str,
    data_type: str,
    descriptor="sci",
    dep_type: str = "DOWNSTREAM",
    relationship: str = "HARD",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    version: Optional[str] = None,
    trigger_type: Optional[str] = None,
):
    """Create event dictionaries for tests."""
    event = {
        "queryStringParameters": {
            "dependency_type": dep_type,
            "relationship": relationship,
            "data_source": source,
            "data_type": data_type,
            "descriptor": descriptor,
        }
    }
    optional_params = {
        "start_date": start_date,
        "end_date": end_date,
        "version": version,
        "trigger_type": trigger_type,
    }
    for param, value in optional_params.items():
        if value:
            event["queryStringParameters"][param] = value

    return event


def _populate_file_catalog(session):
    """Add records to the ScienceFiles table."""
    # Setup: Add records to the database
    test_records = [
        ScienceFiles(
            file_path="/path/to/imap_ultra_l2_sci_20240101_v001.cdf",
            instrument="ultra",
            data_level="l2",
            descriptor="sci",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l0_raw_20240110_v001.pkts",
            instrument="swe",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2024, 1, 10),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l0_raw_20240101_v001.pkts",
            instrument="swe",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240101_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        # Add multiple swe l1a records but with different start dates
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240102_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 1, 2),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240103_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 1, 3),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        # Adding a downstream swe l1b file that depends on the science file above
        ScienceFiles(
            file_path="/path/to/imap_swe_l1b_sci_20240102_v001.cdf",
            instrument="swe",
            data_level="l1b",
            descriptor="sci",
            start_date=datetime(2024, 1, 2),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        # Adding files to test for duplicate job
        ScienceFiles(
            file_path="/path/to/imap_lo_l1a_de_20240101_v001.cdf",
            instrument="lo",
            data_level="l1a",
            descriptor="de",
            start_date=datetime(2010, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_lo_l1a_sci_20240101_v001.cdf",
            instrument="lo",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2010, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_lo_l1a_sci_20240101_v002.cdf",
            instrument="lo",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2010, 1, 1),
            version="v002",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_lo_l1a_sci_20240102_v002.cdf",
            instrument="lo",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2010, 1, 2),
            version="v003",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_l1b-in-flight-cal_20230101_v001.cdf",
            instrument="swe",
            descriptor="l1b-in-flight-cal",
            start_date=datetime(2023, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_l1b-in-flight-cal_20231231_20240102_v002.cdf",
            instrument="swe",
            descriptor="l1b-in-flight-cal",
            start_date=datetime(2023, 12, 31),
            end_date=datetime(2024, 1, 2),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(test_records)
    session.commit()
