"""Tests for the Download API."""

from sds_data_manager.lambda_code.SDSCode import download_api


def test_object_exists(s3_client):
    """Test that we get a presigned url back for an object that exists."""
    science_file = "imap/swe/l1a/2010/01/imap_swe_l1a_test_20100101_v000.cdf"
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=science_file,
        Body=b"test",
    )
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": science_file},
    }
    response = download_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 302
    assert "Location" in response["headers"]
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in response["headers"]["Location"]
    assert "download_url" in response["body"]


def test_nonexistant_object():
    """Test that objects exist in s3 fails."""
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": "bad_path/bad_file.txt"},
    }

    response = download_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 404


def test_input_parameters_missing():
    """Test that required input parameters exist."""
    empty_para_event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        # No pathParameters
    }

    response = download_api.lambda_handler(event=empty_para_event, context=None)
    assert response["statusCode"] == 400
