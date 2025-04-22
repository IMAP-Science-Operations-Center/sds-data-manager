"""Unit test for batch job query API."""

import json
from datetime import datetime

from sds_data_manager.lambda_code.SDSCode.api_lambdas import batch_job_query_api
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.database.models import (
    ProcessingJob,
)


def _populate_processing_table(session):
    """Add test data to database."""
    # Add an inprogress record to the processing table
    # At the time of job kickoff, we only have these written to the table
    records = [
        ProcessingJob(
            status=models.Status.SUCCEEDED,
            instrument="lo",
            data_level="l1b",
            descriptor="de",
            start_date=datetime(2010, 1, 1),
            version="v001",
            job_definition="lo-definition",
            job_log_stream_id="lo-log-stream-id",
            container_image="lo-container-image",
            container_command="lo-some-command",
            started_at=datetime(2010, 1, 1, 20, 21, 9, 388000),
            stopped_at=datetime(2010, 1, 1, 20, 21, 34, 388000),
        ),
        ProcessingJob(
            status=models.Status.INPROGRESS,
            instrument="idex",
            data_level="l1b",
            descriptor="sci",
            start_date=datetime(2010, 1, 2),
            version="v001",
        ),
    ]

    session.add_all(records)
    session.commit()


def test_batch_job_query_api(session):
    """Test batch job query API."""
    # Add test data to database
    _populate_processing_table(session)

    # Check for the case when table has all records in the row
    event = {
        "queryStringParameters": {
            "instrument": "lo",
            "data_level": "l1b",
            "descriptor": "de",
            "start_date": "20100101",
            "version": "v001",
        }
    }
    response = batch_job_query_api.lambda_handler(event, None)
    response_data = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert len(response_data) == 1
    assert response_data[0]["status"] == "SUCCEEDED"
    assert response_data[0]["instrument"] == "lo"
    # Check that batch job parameters exists in the response.
    assert response_data[0].values() is not None

    # Test for the inprogress record
    event = {
        "queryStringParameters": {
            "instrument": "idex",
        }
    }
    response = batch_job_query_api.lambda_handler(event, None)
    response_data = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert len(response_data) == 1
    assert response_data[0]["status"] == "INPROGRESS"
    assert response_data[0]["instrument"] == "idex"
    # Check that batch job parameters doesn't exist in the response.
    assert response_data[0]["job_definition"] is None
    assert response_data[0]["job_log_stream_id"] is None
    assert response_data[0]["container_image"] is None
    assert response_data[0]["container_command"] is None
    assert response_data[0]["started_at"] is None
    assert response_data[0]["stopped_at"] is None

    # Test with nothing passed in. It should return latest n records
    event = {}
    response = batch_job_query_api.lambda_handler(event, None)
    response_data = json.loads(response["body"])
    # Check the response
    assert response["statusCode"] == 200
    assert len(response_data) == 2
    assert response_data[0]["status"] == "INPROGRESS"
    assert response_data[1]["status"] == "SUCCEEDED"
    assert response_data[0]["instrument"] == "idex"
    assert response_data[1]["instrument"] == "lo"

    # Invalid parameter
    event = {"queryStringParameters": {"non-existing-key": "yes"}}

    response = batch_job_query_api.lambda_handler(event, None)
    assert response["statusCode"] == 400
    assert json.loads(response["body"]) == {
        "error": "Invalid parameter: non-existing-key"
    }

    # Empty response
    event = {"queryStringParameters": {"instrument": "swe"}}
    response = batch_job_query_api.lambda_handler(event, None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == []
