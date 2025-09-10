"""Tests for the API utility functions."""

from sds_data_manager.lambda_code.SDSCode.api_lambdas import utils


def test_is_authenticated_user():
    """Test the is_authenticated_user function with various event inputs."""
    # Test with api-key in routeKey
    event1 = {
        "version": "2.0",
        "routeKey": "GET /api-key/query",
        "rawPath": "/api-key/query",
    }
    assert utils.is_authenticated_user(event1) is True

    # Test with auth in rawPath
    event2 = {
        "version": "2.0",
        "routeKey": "GET /authorized/query",
        "rawPath": "/authorized/query",
    }
    assert utils.is_authenticated_user(event2) is True

    # Test with non-authenticated path
    event3 = {
        "version": "2.0",
        "routeKey": "GET /query",
        "rawPath": "/query",
    }
    assert utils.is_authenticated_user(event3) is False

    # Test with empty event
    event4 = {}
    assert utils.is_authenticated_user(event4) is False
