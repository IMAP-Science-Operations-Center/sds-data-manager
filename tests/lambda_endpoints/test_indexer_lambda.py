"""Tests for the indexer lambda."""

import os
from datetime import datetime

import pytest
from imap_data_access import ScienceFilePath
from sqlalchemy import select
from sqlalchemy.orm import Session

from sds_data_manager.lambda_code.SDSCode import indexer
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.database_handler import (
    update_status_table,
)
from sds_data_manager.lambda_code.SDSCode.indexer import (
    send_event_from_indexer,
)


def test_batch_job_event(test_engine, events_client):
    """Test batch job event."""
    # Write to status tracking table with current batch job event info
    status_params = {
        "status": models.Status.INPROGRESS,
        "instrument": "swapi",
        "data_level": "l1",
        "descriptor": "sci-1min",
        "start_date": datetime.strptime("20230724", "%Y%m%d"),
        "version": "v001",
    }
    update_status_table(status_params)

    # TODO: Will update this test further
    # when I extend batch job event handler.
    event = {
        "detail-type": "Batch Job State Change",
        "source": "aws.batch",
        "detail": {
            "jobArn": (
                "arn:aws:batch:us-west-2:012345678910:"
                "job/26242c7e-3d49-4e41-9387-74fcaf9630bb"
            ),
            "jobName": "swe-l0-job",
            "jobId": "26242c7e-3d49-4e41-9387-74fcaf9630bb",
            "jobQueue": (
                "arn:aws:batch:us-west-2:012345678910:"
                "job-queue/swe-fargate-batch-job-queue"
            ),
            "status": "FAILED",
            "statusReason": "some error message",
            "jobDefinition": (
                "arn:aws:batch:us-west-2:012345678910:"
                "job-definition/fargate-batch-job-definitionswe:1"
            ),
            "container": {
                "image": (
                    "123456789012.dkr.ecr.us-west-2.amazonaws.com/" "swapi-repo:latest"
                ),
                "command": [
                    "--instrument",
                    "swapi",
                    "--level",
                    "l1",
                    "--descriptor",
                    "sci-1min",
                    "--start-date",
                    "20230724",
                    "--version",
                    "v001",
                    "--dependency",
                    """[
                        {
                            'instrument': 'swapi',
                            'level': 'l0',
                            'start_date': 20230724,
                            'version': 'v001'
                        }
                    ]""",
                    "--use-remote",
                ],
                "logStreamName": (
                    "fargate-batch-job-definitionswe/default/"
                    "8a2b784c7bd342f69ea5dac3adaed26f"
                ),
            },
        },
    }
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 200

    # check that data was written to status table
    with Session(db.get_engine()) as session:
        query = select(models.StatusTracking.__table__).where(
            models.StatusTracking.instrument == status_params["instrument"],
            models.StatusTracking.data_level == status_params["data_level"],
            models.StatusTracking.version == status_params["version"],
        )

        status_tracking = session.execute(query).first()
        assert status_tracking.status == models.Status.FAILED

    # Test for succeeded case
    event["detail"]["status"] = "SUCCEEDED"
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 200

    with Session(db.get_engine()) as session:
        query = select(models.StatusTracking.__table__).where(
            models.StatusTracking.instrument == status_params["instrument"],
            models.StatusTracking.data_level == status_params["data_level"],
            models.StatusTracking.version == status_params["version"],
        )

        status_tracking = session.execute(query).first()
        assert status_tracking.status == models.Status.SUCCEEDED

    # Test for file that is not in status table
    event["detail"]["container"]["command"][1] = "swe"
    result = indexer.lambda_handler(event=event, context={})
    assert result["statusCode"] == 200

    with Session(db.get_engine()) as session:
        query = select(models.StatusTracking.__table__).where(
            models.StatusTracking.instrument == "swe"
        )

        status_tracking = session.execute(query).first()
        assert status_tracking.status == models.Status.SUCCEEDED


def test_s3_event(test_engine, events_client):
    """Test s3 event."""
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
                    "imap/hit/l0/2024/01/" "imap_hit_l0_sci-test_20240101_v001.pkts"
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
            == "imap/hit/l0/2024/01/imap_hit_l0_sci-test_20240101_v001.pkts"
        )
        assert result[0].data_level == "l0"
        assert result[0].instrument == "hit"
        assert result[0].extension == "pkts"

    # Test for bad filename input
    event["detail"]["object"]["key"] = (
        "imap/hit/l0/2024/01/" "imap_hit_l0_sci-test_20240101_v001.cdf"
    )

    expected_msg = (
        "Invalid extension. Extension should be pkts for data level l0"
        " and cdf for data level higher than l0 \n"
    )

    with pytest.raises(ScienceFilePath.InvalidScienceFileError) as excinfo:
        ScienceFilePath(os.path.basename(event["detail"]["object"]["key"]))
    # Wrote this test outside because pre-commit complains
    assert str(excinfo.value) == expected_msg


def test_unknown_event(test_engine):
    """Test for unknown event source."""
    event = {"source": "test"}
    returned_value = indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 400
    assert returned_value["body"] == "Unknown event source"


def test_send_lambda_put_event(events_client):
    """Test the ``send_event_from_indexer`` function."""
    filename = "imap_swapi_l1_sci-1min_20230724_v001.cdf"

    result = send_event_from_indexer(filename)
    assert result["ResponseMetadata"]["HTTPStatusCode"] == 200
