"""Tests for the SPICE indexer lambda."""

import os
from datetime import datetime

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import spice_indexer


def put_local_file_in_bucket(s3_client, path_in_s3, path_local):
    """Put the a local file into a test bucket, and return a mock event notification.

    Parameters
    ----------
    s3_client
        The s3 client to use
    path_in_s3: str
        The path to place the file in S3
    path_local: str
        The local path of the file to upload

    Returns
    -------
    s3_event: dict
        A dictionary mocking the s3 put event notification

    """
    with open(path_local, "rb") as f:
        s3_client.put_object(
            Bucket="test-data-bucket",
            Key=path_in_s3,
            Body=f,
        )
    event = {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": datetime.now().isoformat(),
        "detail": {
            "version": "0",
            "bucket": {"name": "test-data-bucket"},
            "object": {
                "key": (path_in_s3),
                "reason": "PutObject",
            },
        },
    }
    return event


def test_s3_spice_files(session, s3_client, events_client):
    """Test s3 event.

    The following test mimics a leapsecond kernel being placed on the SDS,
    followed by a spacecraft clock kernel, and then an attitude file.

    The files are located in the "test_spice_files" directory.

    """
    current_path = os.path.dirname(os.path.abspath(__file__))

    # Insert leapsecond spice kernel
    leapsecond_event = put_local_file_in_bucket(
        s3_client,
        "spice/lsk/naif0012.tls",
        current_path + "/test_spice_files/naif0012.tls",
    )
    spice_indexer.lambda_handler(leapsecond_event, None)

    # Insert spacecraft clock spice kernel
    clock_kernel_event = put_local_file_in_bucket(
        s3_client,
        "spice/sclk/imapsclk_0012.tsc",
        current_path + "/test_spice_files/imapsclk_0012.tsc",
    )
    spice_indexer.lambda_handler(clock_kernel_event, None)

    # Insert a new attitude kernel
    attitude_kernel_event = put_local_file_in_bucket(
        s3_client,
        "spice/ck/imap_2025_118_2025_120_001.ah.bc",
        current_path + "/test_spice_files/imap_2025_118_2025_120_001.ah.bc",
    )
    spice_indexer.lambda_handler(attitude_kernel_event, None)

    # Verify that the file was moved to the /tmp directory
    # (conftest.py sets the SPICE directory as "tmp")
    assert os.path.exists("/tmp/lsk/naif0012.tls")
    assert os.path.exists("/tmp/sclk/imapsclk_0012.tsc")
    assert os.path.exists("/tmp/ck/imap_2025_118_2025_120_001.ah.bc")

    # Verify that the database was populated appropriately
    result = session.query(models.SPICEFiles).all()

    # Verify the database has 3 files:
    assert len(result) == 3

    # Loop through the 3 database entries and ensure their accuracy
    leapsecond_kernel_reached = False
    clock_kernel_reached = False
    attitude_kernel_reached = False
    for r in result:
        print(r.file_name)
        if r.file_name == "imap_2025_118_2025_120_001.ah.bc":
            attitude_kernel_reached = True
            assert r.kernel_type == "attitude_history"
            assert r.version == 1
            assert len(r.file_intervals_datetime) == 2  # 1 significant gap detected
        if r.file_name == "naif0012.tls":
            leapsecond_kernel_reached = True
            assert r.kernel_type == "leapseconds"
            assert r.version == 12
            assert len(r.file_intervals_datetime) == 1  # Default time range
        if r.file_name == "imapsclk_0012.tsc":
            clock_kernel_reached = True
            assert r.kernel_type == "spacecraft_clock"
            assert r.version == 12
            assert len(r.file_intervals_datetime) == 1  # Default time range

    assert clock_kernel_reached  # clock kernel found in database
    assert leapsecond_kernel_reached  # leapsecond kernel found in database
    assert attitude_kernel_reached  # attitude kernel found in database


"""
    file_path = Column(String, nullable=False, primary_key=True, unique=True)
    ingestion_date = Column(DateTime(timezone=True))
    file_root = Column(String)
    kernel_type = Column(String)
    min_date_j2000 = Column(Float)
    max_date_j2000 = Column(Float)
    file_intervals_j2000 = Column(JSON)
    min_date_datetime = Column(DateTime(timezone=True))
    max_date_datetime = Column(DateTime(timezone=True))
    file_intervals_datetime = Column(JSON)
    min_date_sclk = Column(String)
    max_date_sclk = Column(String)
    file_intervals_sclk = Column(JSON)
    sclk_kernel = Column(String)
    lsk_kernel = Column(String)
    version = Column(Integer, nullable=True)'
    """
