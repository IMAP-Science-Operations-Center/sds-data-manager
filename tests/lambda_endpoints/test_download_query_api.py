from pathlib import Path

import pytest

from sds_data_manager.lambda_code.SDSCode.download_query_api import lambda_handler

BUCKET_NAME = "test-bucket"
TEST_FILE = "science_block_20221116_163611Z_idle.bin"


@pytest.fixture(autouse=True)
def setup_s3(s3_client):
    """Populate the mocked s3 client with a bucket and a file

    Each test below will use this fixture by default
    """
    s3_client.create_bucket(
        Bucket=BUCKET_NAME,
    )
    result = s3_client.list_buckets()
    assert len(result["Buckets"]) == 1
    assert result["Buckets"][0]["Name"] == BUCKET_NAME

    # upload a file
    local_filepath = Path(__file__).parent.parent.resolve() / f"test-data/{TEST_FILE}"
    s3_client.upload_file(local_filepath, BUCKET_NAME, TEST_FILE)
    file_list = s3_client.list_objects(Bucket=BUCKET_NAME)["Contents"]
    assert len(file_list) == 1
    return s3_client


def test_object_exists_with_s3_uri():
    """Test that this object exists within s3"""
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "rawQueryString": f"s3_uri=s3://{BUCKET_NAME}/{TEST_FILE}",
        "queryStringParameters": {"s3_uri": f"s3://{BUCKET_NAME}/{TEST_FILE}"},
    }

    response = lambda_handler(event=event, context=None)
    assert response["statusCode"] == 200
    assert "download_url" in response["body"]


def test_object_exists_with_s3_uri_fails():
    """Test that objects exist in s3 fails"""
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "rawQueryString": f"s3_uri=s3://{BUCKET_NAME}/bad_path/bad_file.txt",
        "queryStringParameters": {
            "s3_uri": f"s3://{BUCKET_NAME}/bad_path/bad_file.txt"
        },
    }

    response = lambda_handler(event=event, context=None)
    assert response["statusCode"] == 404


def test_input_parameters_missing():
    """Test that required input parameters exist"""
    empty_para_event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "rawQueryString": "",
    }

    bad_para_event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "rawQueryString": f"bad_input={TEST_FILE}",
        "queryStringParameters": {"bad_input": f"{TEST_FILE}"},
    }

    response = lambda_handler(event=empty_para_event, context=None)
    assert response["statusCode"] == 400

    response = lambda_handler(event=bad_para_event, context=None)
    assert response["statusCode"] == 400
