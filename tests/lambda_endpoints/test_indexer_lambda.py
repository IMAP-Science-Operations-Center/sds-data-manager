"""Test indexer lambda"""


import os

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from sds_data_manager.lambda_code.SDSCode import indexer
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.indexer import (
    get_dependency,
    send_event_from_indexer,
)
from sds_data_manager.lambda_code.SDSCode.path_helper import (
    InvalidScienceFileError,
    ScienceFilepathManager,
)


@pytest.fixture()
def populate_db(test_engine):
    """Populate database with test data"""
    test_data = [
        models.PreProcessingDependency(
            primary_instrument="swapi",
            primary_data_level="l2",
            primary_descriptor="sci-1m",
            dependent_instrument="swapi",
            dependent_data_level="l1",
            dependent_descriptor="hk",
            relationship="HARD",
            direction="UPSTREAM",
        ),
        models.PreProcessingDependency(
            primary_instrument="swe",
            primary_data_level="l2",
            primary_descriptor="sci",
            dependent_instrument="glows",
            dependent_data_level="l3",
            dependent_descriptor="sci",
            relationship="HARD",
            direction="DOWNSTREAM",
        ),
        models.PreProcessingDependency(
            primary_instrument="swe",
            primary_data_level="l1b",
            primary_descriptor="sci",
            dependent_instrument="mag",
            dependent_data_level="l2",
            dependent_descriptor="sci",
            relationship="HARD",
            direction="UPSTREAM",
        ),
        models.PreProcessingDependency(
            primary_instrument="swe",
            primary_data_level="l1b",
            primary_descriptor="sci",
            dependent_instrument="hi-45",
            dependent_data_level="l1c",
            dependent_descriptor="sci",
            relationship="SOFT",
            direction="DOWNSTREAM",
        ),
        models.PreProcessingDependency(
            primary_instrument="swe",
            primary_data_level="l1b",
            primary_descriptor="sci",
            dependent_instrument="lo",
            dependent_data_level="l1c",
            dependent_descriptor="sci",
            relationship="SOFT",
            direction="DOWNSTREAM",
        ),
        models.PreProcessingDependency(
            primary_instrument="swe",
            primary_data_level="l1b",
            primary_descriptor="sci",
            dependent_instrument="ultra-45",
            dependent_data_level="l1c",
            dependent_descriptor="sci",
            relationship="SOFT",
            direction="DOWNSTREAM",
        ),
        models.PreProcessingDependency(
            primary_instrument="codice",
            primary_data_level="l1b",
            primary_descriptor="sci",
            dependent_instrument="hi-45",
            dependent_data_level="l1c",
            dependent_descriptor="sci",
            relationship="SOFT",
            direction="DOWNSTREAM",
        ),
        models.PreProcessingDependency(
            primary_instrument="codice",
            primary_data_level="l1b",
            primary_descriptor="sci",
            dependent_instrument="lo",
            dependent_data_level="l1c",
            dependent_descriptor="sci",
            relationship="SOFT",
            direction="DOWNSTREAM",
        ),
        models.PreProcessingDependency(
            primary_instrument="codice",
            primary_data_level="l1b",
            primary_descriptor="sci",
            dependent_instrument="ultra-45",
            dependent_data_level="l1c",
            dependent_descriptor="sci",
            relationship="SOFT",
            direction="DOWNSTREAM",
        ),
        models.PreProcessingDependency(
            primary_instrument="hit",
            primary_data_level="l2",
            primary_descriptor="sci",
            dependent_instrument="glows",
            dependent_data_level="l3",
            dependent_descriptor="sci",
            relationship="HARD",
            direction="DOWNSTREAM",
        ),
    ]

    with Session(db.get_engine()) as session:
        session.add_all(test_data)
        session.commit()
        yield session


@pytest.fixture()
def write_to_s3(s3_client):
    """Write test data to s3"""
    # first create test bucket
    s3_client.create_bucket(
        Bucket="test-data-bucket",
        CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
    )
    # write file to s3
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=("imap/swapi/l1/2023/01/imap_swapi_l1_sci-1m_20230724_20230724_v02-01.cdf"),
        Body=b"test",
    )
    return s3_client


def test_batch_job_event(test_engine, write_to_s3, events_client, set_env):
    """Test batch job event"""
    # Send s3 event first to write initial data to satus
    # table
    custom_event = {
        "detail-type": "Job Started",
        "source": "imap.lambda",
        "detail": {
            "file_path_to_create": (
                "imap/swapi/l1/2023/01/"
                "imap_swapi_l1_sci-1m_20230724_20230724_v02-01.cdf"
            ),
            "status": "INPROGRESS",
            "dependency": {"codice": "s3-filepath", "mag": "s3-filepath"},
        },
    }

    # Test for good event
    returned_value = indexer.lambda_handler(event=custom_event, context={})
    assert returned_value["statusCode"] == 200

    # TODO: Will update this test further
    # when I extend batch job event handler.
    event = {
        "detail-type": "Batch Job State Change",
        "source": "aws.batch",
        "detail": {
            "jobName": "test-batch-tenzin",
            "jobDefinition": "test-batch-job-definition",
            "status": "FAILED",
            "statusReason": "some error message",
            "container": {
                "image": (
                    "123456789012.dkr.ecr.us-west-2.amazonaws.com/" "swapi-repo:latest"
                ),
                "command": [
                    "--instrument",
                    "swapi",
                    "--level",
                    "l1",
                    "--file_path",
                    (
                        "imap/swapi/l1/2023/01/"
                        "imap_swapi_l1_sci-1m_20230724_20230724_v02-01.cdf"
                    ),
                    "--dependency",
                    "[{'instrument': 'swapi', 'level': 'l0', 'version': 'v02-01'}]",
                ],
            },
        },
    }
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 200

    with Session(db.get_engine()) as session:
        file_path = custom_event["detail"]["file_path_to_create"]
        query = select(models.StatusTracking.__table__).where(
            models.StatusTracking.file_path_to_create == file_path
        )

        status_tracking = session.execute(query).first()
        assert status_tracking.status == models.Status.FAILED
        assert status_tracking.ingestion_date is None

    # Test for succeeded case
    event["detail"]["status"] = "SUCCEEDED"
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 200

    with Session(db.get_engine()) as session:
        file_path = custom_event["detail"]["file_path_to_create"]
        query = select(models.StatusTracking.__table__).where(
            models.StatusTracking.file_path_to_create == file_path
        )

        status_tracking = session.execute(query).first()
        assert status_tracking.status == models.Status.SUCCEEDED
        assert status_tracking.ingestion_date is not None


def test_pre_processing_dependency(test_engine, populate_db):
    """Test pre-processing dependency"""
    swe_dependency = get_dependency(
        instrument="swe",
        data_level="l1b",
        descriptor="sci",
        direction="DOWNSTREAM",
        relationship="SOFT",
    )

    expected_instruments = ["hi-45", "lo", "ultra-45"]
    for row in swe_dependency:
        assert row.dependent_instrument in expected_instruments

    swapi_dependency = get_dependency(
        instrument="swapi",
        data_level="l2",
        descriptor="sci-1m",
        direction="UPSTREAM",
        relationship="HARD",
    )
    assert len(swapi_dependency) == 1
    assert swapi_dependency[0].dependent_instrument == "swapi"
    assert swapi_dependency[0].dependent_data_level == "l1"
    assert swapi_dependency[0].dependent_descriptor == "hk"


def test_custom_lambda_event(test_engine):
    """Test custom PutEvent from lambda"""
    # Took out unused parameters from event
    event = {
        "detail-type": "Job Started",
        "source": "imap.lambda",
        "detail": {
            "file_path_to_create": (
                "imap/swapi/l1/2023/01/"
                "imap_swapi_l1_sci-1m_20230724_20230724_v02-01.cdf"
            ),
            "status": "INPROGRESS",
            "dependency": {"codice": "s3-filepath", "mag": "s3-filepath"},
        },
    }

    # Test for good event
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 200

    # Check that data was written to database by lambda
    with Session(db.get_engine()) as session:
        result = session.query(models.StatusTracking).all()
        assert len(result) == 1
        assert (
            result[0].file_path_to_create
            == "imap/swapi/l1/2023/01/imap_swapi_l1_sci-1m_20230724_20230724_v02-01.cdf"
        )
        assert result[0].status == models.Status.INPROGRESS


def test_s3_event(test_engine, events_client):
    """Test s3 event"""
    # Took out unused parameters from event
    event = {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": "2024-01-16T17:35:08Z",
        "detail": {
            "version": "0",
            "bucket": {"name": "sds-data-123456789012"},
            "object": {
                "key": (
                    "imap/hit/l0/2024/01/"
                    "imap_hit_l0_sci-test_20240101_20240104_v02-01.pkts"
                ),
                "reason": "PutObject",
            },
        },
    }
    # Test for good event
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 200

    # Check that data was written to database by lambda
    with Session(db.get_engine()) as session:
        result = session.query(models.FileCatalog).all()
        assert len(result) == 1
        assert (
            result[0].file_path
            == "imap/hit/l0/2024/01/imap_hit_l0_sci-test_20240101_20240104_v02-01.pkts"
        )
        assert result[0].data_level == "l0"
        assert result[0].instrument == "hit"
        assert result[0].extension == "pkts"

    # Test for bad filename input
    event["detail"]["object"]["key"] = (
        "imap/hit/l0/2024/01/" "imap_hit_l0_sci-test_20240101_20240104_v02-01.cdf"
    )

    expected_msg = (
        "Invalid extension. Extension should be pkts for data level l0"
        " and cdf for data level higher than l0"
    )

    with pytest.raises(InvalidScienceFileError) as excinfo:
        ScienceFilepathManager(os.path.basename(event["detail"]["object"]["key"]))
    # Wrote this test outside because pre-commit complains
    assert str(excinfo.value) == expected_msg

    # Test for higher data level input
    event["detail"]["object"]["key"] = (
        "imap/hit/l1a/2024/01/" "imap_hit_l1a_sci-test_20240101_20240104_v02-01.cdf"
    )

    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 400
    assert returned_value["body"] == "Invalid data level"


def test_unknown_event(test_engine):
    """Test for unknown event source"""
    event = {"source": "test"}
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 400
    assert returned_value["body"] == "Unknown event source"


def test_send_lambda_put_event(events_client):
    filename = "imap_swapi_l1_sci-1m_20230724_20230724_v02-01.cdf"

    result = send_event_from_indexer(filename)
    assert result["ResponseMetadata"]["HTTPStatusCode"] == 200
