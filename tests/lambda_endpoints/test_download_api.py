"""Tests for the Download API."""

from sds_data_manager.lambda_code.SDSCode import download_api


def test_object_exists(spice_file):
    """Test that this object exists within s3."""
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": spice_file},
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
