"""Tests for the indexer lambda."""

from datetime import datetime

from imap_data_access import ScienceFilePath

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import indexer
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.indexer import (
    send_event_from_indexer,
)


def test_s3_sci_event(session, s3_client, events_client):
    """Test s3 event."""
    filepath = "imap/hit/l0/2024/01/imap_hit_l0_sci-test_20240101_v001.0001.pkts"
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=filepath,
        Body=b"test",
    )
    event = {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": "2024-01-16T17:35:08Z",
        "detail": {
            "version": "0",
            "bucket": {"name": "test-data-bucket"},
            "object": {
                "key": (filepath),
                "reason": "PutObject",
            },
        },
    }
    # Test for good event
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 200

    # Check that data was written to database by lambda
    result = session.query(models.ScienceFiles).all()
    assert len(result) == 1
    assert (
        result[0].file_path
        == "imap/hit/l0/2024/01/imap_hit_l0_sci-test_20240101_v001.0001.pkts"
    )
    assert result[0].data_level == "l0"
    assert result[0].instrument == "hit"
    assert result[0].extension == "pkts"
    assert result[0].major_version == 1
    assert result[0].minor_version == 1


def test_s3_cr_event(session, s3_client, events_client):
    """Test s3 event."""
    filepath = (
        "imap/glows/l3a/2024/01/imap_glows_l3a_sci-test_20240101-cr02025_v001.0001.cdf"
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=filepath,
        Body=b"test",
    )
    event = {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": "2024-01-16T17:35:08Z",
        "detail": {
            "version": "0",
            "bucket": {"name": "test-data-bucket"},
            "object": {
                "key": (filepath),
                "reason": "PutObject",
            },
        },
    }
    # Test for good event
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 200

    # Check that data was written to database by lambda
    result = session.query(models.ScienceFiles).all()
    assert len(result) == 1
    assert result[0].file_path == (
        "imap/glows/l3a/2024/01/imap_glows_l3a_sci-test_20240101-cr02025_v001.0001.cdf"
    )
    assert result[0].data_level == "l3a"
    assert result[0].instrument == "glows"
    assert result[0].extension == "cdf"
    assert result[0].cr == 2025
    assert result[0].major_version == 1
    assert result[0].minor_version == 1


def test_s3_anc_event(session, s3_client, events_client):
    """Test s3 event."""
    filepath = "imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20240101_v001.cdf"
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=filepath,
        Body=b"test",
    )
    event = {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": "2024-01-16T17:35:08Z",
        "detail": {
            "version": "0",
            "bucket": {"name": "test-data-bucket"},
            "object": {
                "key": (filepath),
                "reason": "PutObject",
            },
        },
    }
    # Test for good event
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 200

    # Check that data was written to database by lambda
    result = session.query(models.AncillaryFiles).all()
    assert len(result) == 1
    assert result[0].file_path == filepath
    assert result[0].instrument == "swe"
    assert result[0].extension == "cdf"


def test_unknown_event(session):
    """Test for unknown event source."""
    event = {"source": "test"}
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 400
    assert returned_value["body"] == "Unknown event source"


def test_send_lambda_put_event(events_client):
    """Test the ``send_event_from_indexer`` function."""
    filename = "imap_swapi_l1_sci-1min_20230724_v001.0001.cdf"
    file_obj = ScienceFilePath(filename)

    result = send_event_from_indexer(file_obj)
    assert result["ResponseMetadata"]["HTTPStatusCode"] == 200


def test_s3_release_event(session, s3_client):
    """Test s3 event for release files.

    Release files should be written to ReleaseFiles table and return 200
    without sending an EventBridge event (no further pipeline processing).
    """
    filename = "imap_swe_early-release_20240101_20240201_v001.txt"
    filepath = f"imap/release/{filename}"

    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=filepath,
        Body=b"test release data",
    )

    event = {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": "2024-01-16T17:35:08Z",
        "detail": {
            "version": "0",
            "bucket": {"name": "test-data-bucket"},
            "object": {
                "key": filepath,
                "reason": "PutObject",
            },
        },
    }

    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 200

    # Confirm written to ReleaseFiles, not AncillaryFiles
    release_result = session.query(models.ReleaseFiles).all()
    assert len(release_result) == 1
    assert release_result[0].file_path == filepath
    assert release_result[0].start_date == datetime.strptime("20240101", "%Y%m%d")
    assert release_result[0].end_date == datetime.strptime("20240201", "%Y%m%d")
    assert release_result[0].instrument == "swe"
    assert release_result[0].descriptor == "early-release"
    assert release_result[0].extension == "txt"

    anc_result = session.query(models.AncillaryFiles).all()
    assert len(anc_result) == 0


def test_s3_quicklook_event(session, s3_client, events_client):
    """Test s3 event for quicklook files."""
    # Use a clearly identifiable quicklook file pattern
    filename = "imap_hit_l2_ql-survey_20240101_v001.0001.png"
    filepath = f"imap/hit/l2/ql/2024/01/{filename}"

    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=filepath,
        Body=b"test image data",
    )

    event = {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": "2024-01-16T17:35:08Z",
        "detail": {
            "version": "0",
            "bucket": {"name": "test-data-bucket"},
            "object": {
                "key": (filepath),
                "reason": "PutObject",
            },
        },
    }

    # Test for good event
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 200

    # Check that data was written to database by lambda
    result = session.query(models.QuicklookFiles).all()
    assert len(result) == 1
    assert result[0].file_path == filepath
    assert result[0].data_level == "l2"
    assert result[0].instrument == "hit"
    assert result[0].major_version == 1
    assert result[0].minor_version == 1
    assert result[0].extension == "png"
    assert result[0].descriptor == "ql-survey"


def test_idex_l0_event(session, s3_client, events_client):
    """Test s3 event for idex l0 files."""
    # Use a clearly identifiable IDEX L0 file pattern
    filename = "imap_idex_l0_raw_20250101_v001.0001.pkts"
    filepath = f"imap/idex/l0/{filename}"

    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=filepath,
        Body=b"test image data",
    )

    event = {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": "2024-01-16T17:35:08Z",
        "detail": {
            "version": "0",
            "bucket": {"name": "test-data-bucket"},
            "object": {
                "key": (filepath),
                "reason": "PutObject",
            },
        },
    }

    # Test for good event
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 200
    assert returned_value["body"] == (
        "Received an IDEX L0 file"
        " imap_idex_l0_raw_20250101_v001.0001.pkts. This file will "
        "be indexed in a separate "
        "lambda. See idex-l0-file-indexer lambda for details."
    )
