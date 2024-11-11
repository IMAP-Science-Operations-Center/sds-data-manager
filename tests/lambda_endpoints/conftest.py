"""Setup testing environment to test lambda handler code."""

from unittest.mock import patch

import boto3
import pytest
from moto import mock_events, mock_s3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database.models import Base

BUCKET_NAME = "test-data-bucket"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    """Set global environment variables."""
    monkeypatch.setenv("S3_BUCKET", BUCKET_NAME)
    # Mock AWS Credentials for moto
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture(scope="module")
def science_file():
    """Path to a valid science file."""
    return "imap/swe/l1a/2010/01/imap_swe_l1a_test-description_20100101_v000.cdf"


@pytest.fixture(scope="module")
def spice_file():
    """Path to a valid spice file."""
    return "spice/ck/test_v000.bc"


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
