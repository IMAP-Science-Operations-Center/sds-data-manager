"""Setup testing environment to test lambda handler code."""

import json
import os
from datetime import datetime
from unittest.mock import Mock, patch

import boto3
import pytest
from moto import mock_ecr, mock_events, mock_s3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sds_data_manager.lambda_code.SDSCode.api_lambdas import upload_api
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database.models import (
    Base,
    SPICEFiles,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import batch_starter

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


@pytest.fixture(scope="module")
def ancillary_file():
    """Path to a valid ancillary file."""
    return "imap/ancillary/swe/imap_swe_test-ancillary-description_20100101_v000.cdf"


@pytest.fixture(scope="module")
def science_file():
    """Path to a valid science file."""
    return "imap/swe/l1a/2010/01/imap_swe_l1a_test-description_20100101_v000.cdf"


@pytest.fixture(scope="module")
def dependency_file():
    """Path to a valid dependency file."""
    return (
        "imap/dependency/ultra/l2/2025/03/imap_ultra_l2_u45-ena-h-hf-nsp-test-hae-6deg"
        "-3mo-4d649e314e8ac32e3fb76fe5d5aad46f_20250301_v001.json"
    )


@pytest.fixture(scope="module")
def spice_file():
    """Path to a valid spice file."""
    return "imap/spice/ck/imap_2025_032_2025_034_003.ah.bc"


@pytest.fixture(scope="module")
def invalid_file():
    """Path for an invalid file."""
    return (
        "imap/swe/l1a/2010/01/imap_swe_l1a_test-description_"
        "second-description_20100101_v000.cdf"
    )


@pytest.fixture(autouse=True)
def s3_client():
    """Mock S3 Client, so we don't need network requests."""
    with mock_s3():
        s3_client = boto3.client("s3", region_name="us-east-1")

        s3_client.create_bucket(
            Bucket=BUCKET_NAME,
        )

        yield s3_client


@pytest.fixture
def events_client():
    """Mock EventBridge client."""
    with mock_events():
        yield boto3.client("events", region_name="us-west-2")


@pytest.fixture
def ecr_client():
    """Mock ECR client."""
    with mock_ecr():
        ecr_client = boto3.client("ecr", region_name="us-west-2")
        # Create a mock repository for swapi
        ecr_client.create_repository(repositoryName="swapi-repo")
        ecr_client.put_image(
            registryId="123456789012",
            repositoryName="swapi-repo",
            imageManifest=json.dumps({}),
            imageManifestMediaType="json",
            imageTag="latest",
            imageDigest="sha256:123exampledigest",
        )
        yield ecr_client


@pytest.fixture
def mock_upload_request_success():
    """Fixture to mock upload_api and requests.put for successful uploads."""
    with (
        patch.object(batch_starter, "upload_api") as mock_upload_api,
        patch(
            "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter.requests"
        ) as mock_requests,
    ):
        mock_upload_api.lambda_handler.return_value = {
            "statusCode": 200,
            "body": json.dumps(
                "https://s3.amazonaws.com/bucket/presigned-url?signature=test"
            ),
        }
        mock_requests.put.return_value = Mock(status_code=200)
        yield mock_upload_api, mock_requests


# Check if `psycopg` and PostgreSQL are both available and compatible.
POSTGRES_AVAILABLE = False
# TODO: fix this to work with postgres locally


# NOTE: The default scope is function, so each test function will
#       get a new database session and start fresh each time.
@pytest.fixture
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


def _static_spice_files(session):
    """Populate the SPICEFiles table with static SPICE files."""
    # Common SPICE files:
    # leapseconds
    # spacecraft clock
    # imap frames
    # science frames
    # pointing attitude
    common_spice_records = [
        SPICEFiles(
            file_name="naif0012.tls",
            file_path="path/to/naif0012.tls",
            ingestion_date=datetime.strptime(
                "2025-04-30 18:24:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_root="naif.tls",
            kernel_type="leapseconds",
            min_date_j2000=0,
            max_date_j2000=4575787269.183866,
            file_intervals_j2000=[[0, 4575787269.183866]],
            min_date_datetime=datetime.strptime(
                "2000-01-01 12:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            max_date_datetime=datetime.strptime(
                "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_intervals_datetime="[[2000-01-01T12:00:00, 2145-01-01T00:00:00]]",
            min_date_sclk="1/0000000000:00000",
            max_date_sclk="1/4285909749:39444",
            file_intervals_sclk="[[1/0000000000:00000, 1/4285909749:39444]]",
            sclk_kernel="/mnt/data/imap/spice/sclk/imap_sclk_0001.tsc",
            lsk_kernel="/mnt/data/imap/spice/lsk/naif0012.tls",
            version=12,
        ),
        SPICEFiles(
            file_name="imap_sclk_0000.tsc",
            file_path="path/to/imap_sclk_0000.tsc",
            ingestion_date=datetime.strptime(
                "2025-04-30 18:24:01+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_root="imap_sclk_0000.tsc",
            kernel_type="spacecraft_clock",
            min_date_j2000=315576066.1839245,
            max_date_j2000=4575787269.183866,
            file_intervals_j2000=[[315576066.1839245, 4575787269.183866]],
            min_date_datetime=datetime.strptime(
                "2010-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            max_date_datetime=datetime.strptime(
                "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_intervals_datetime="[[2010-01-01T00:00:00, 2145-01-01T00:00:00]]",
            min_date_sclk="1/0000000000:00000",
            max_date_sclk="1/4285909749:39444",
            file_intervals_sclk="[[1/0000000000:00000, 1/4285909749:39444]]",
            sclk_kernel="/mnt/data/imap/spice/sclk/imap_sclk_0001.tsc",
            lsk_kernel="/mnt/data/imap/spice/lsk/naif0012.tls",
            version=0,
        ),
        SPICEFiles(
            file_name="imap_000.tf",
            file_path="path/to/imap_000.tf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_root="imap_.tf",
            kernel_type="imap_frames",
            min_date_j2000=0,
            max_date_j2000=4575787269.183866,
            file_intervals_j2000=[[0, 4575787269.183866]],
            min_date_datetime=datetime.strptime(
                "2000-01-01 12:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            max_date_datetime=datetime.strptime(
                "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_intervals_datetime="[[2000-01-01T12:00:00, 2145-01-01T00:00:00]]",
            min_date_sclk="1/0000000000:00000",
            max_date_sclk="1/4285909749:39444",
            file_intervals_sclk="[[1/0000000000:00000, 1/4285909749:39444]]",
            sclk_kernel="/mnt/data/imap/spice/sclk/imap_sclk_0001.tsc",
            lsk_kernel="/mnt/data/imap/spice/lsk/naif0012.tls",
            version=0,
        ),
        SPICEFiles(
            file_path="path/to/imap_science_000.tf",
            file_name="imap_science_000.tf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_root="imap_science_.tf",
            kernel_type="science_frames",
            min_date_j2000=0,
            max_date_j2000=4575787269.183866,
            file_intervals_j2000=[[0, 4575787269.183866]],
            min_date_datetime=datetime.strptime(
                "2000-01-01 12:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            max_date_datetime=datetime.strptime(
                "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_intervals_datetime="[[2000-01-01T00:00:00, 2145-01-01T00:00:00]]",
            min_date_sclk="1/0000000000:00000",
            max_date_sclk="1/4285909749:39444",
            file_intervals_sclk="[[1/0000000000:00000, 1/4285909749:39444]]",
            sclk_kernel="/mnt/data/imap/spice/sclk/imap_sclk_0001.tsc",
            lsk_kernel="/mnt/data/imap/spice/lsk/naif0012.tls",
            version=0,
        ),
        # de###.bsp
        SPICEFiles(
            file_path="path/to/de440.bsp",
            file_name="de440.bsp",
            ingestion_date=datetime.strptime(
                "2025-04-30 18:24:02+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_root="de440.bsp",
            kernel_type="planetary_ephemeris",
            min_date_j2000=0,
            max_date_j2000=4575787269.183866,
            file_intervals_j2000=[[0, 4575787269.183866]],
            min_date_datetime=datetime.strptime(
                "2000-01-01 12:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            max_date_datetime=datetime.strptime(
                "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_intervals_datetime="[[2000-01-01T12:00:00, 2145-01-01T00:00:00]]",
            min_date_sclk="1/0000000000:00000",
            max_date_sclk="1/4285909749:39444",
            file_intervals_sclk="[[1/0000000000:00000, 1/4285909749:39444]]",
            sclk_kernel="/mnt/data/imap/spice/sclk/imap_sclk_0001.tsc",
            lsk_kernel="/mnt/data/imap/spice/lsk/naif0012.tls",
            version=440,
        ),
    ]
    session.add_all(common_spice_records)
    session.commit()
