"""Tests for the API utility functions."""

from sds_data_manager.lambda_code.SDSCode.api_lambdas import utils


def test_is_authenticated_path():
    """Test the is_authenticated_path function with various event inputs."""
    # Test with api-key in routeKey
    event1 = {
        "version": "2.0",
        "routeKey": "GET /api-key/query",
        "rawPath": "/api-key/query",
    }
    assert utils.is_authenticated_path(event1) is True

    # Test with auth in rawPath
    event2 = {
        "version": "2.0",
        "routeKey": "GET /query",
        "rawPath": "/auth/query",
    }
    assert utils.is_authenticated_path(event2) is True

    # Test with non-authenticated path
    event3 = {
        "version": "2.0",
        "routeKey": "GET /query",
        "rawPath": "/query",
    }
    assert utils.is_authenticated_path(event3) is False

    # Test with empty event
    event4 = {}
    assert utils.is_authenticated_path(event4) is False

    # Test with None values
    event5 = {"routeKey": None, "rawPath": None}
    assert utils.is_authenticated_path(event5) is False


def test_filter_files():
    """Test the filter_files function with various inputs."""
    # Create some mock search results
    search_results = [
        {"file_path": "test/file1.txt", "released": True},
        {"file_path": "test/file2.txt", "released": False},
        {"file_path": "test/file3.txt", "released": True},
    ]

    # Test with authenticated path - should return all files
    auth_event = {
        "version": "2.0",
        "routeKey": "GET /api-key/query",
        "rawPath": "/api-key/query",
    }
    filtered = utils.filter_files(auth_event, search_results)
    assert len(filtered) == 3
    assert filtered[0]["file_path"] == "test/file1.txt"
    assert filtered[1]["file_path"] == "test/file2.txt"
    assert filtered[2]["file_path"] == "test/file3.txt"

    # Test with non-authenticated path - should filter out unreleased files
    non_auth_event = {
        "version": "2.0",
        "routeKey": "GET /query",
        "rawPath": "/query",
    }
    filtered = utils.filter_files(non_auth_event, search_results)
    assert len(filtered) == 2
    assert filtered[0]["file_path"] == "test/file1.txt"
    assert filtered[1]["file_path"] == "test/file3.txt"

    # Test with empty results
    filtered = utils.filter_files(auth_event, [])
    assert len(filtered) == 0
