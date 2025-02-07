"""Tests for the I-ALiRT DB Query API Lambda function."""

import json

import pytest

from sds_data_manager.lambda_code.IAlirtCode import ialirt_db_query_api


@pytest.fixture()
def algorithm_table(setup_dynamodb):
    """Return the mocked imap-algorithm-table and populate it with sample data."""
    table = setup_dynamodb["algorithm_table"]

    sample_data = [
        {
            "apid": 478,
            "met": 101,
            "product_name": "hit_product_1",
            "insert_time": "2021-01-01T00:00:00Z",
            "data": "item1",
        },
        {
            "apid": 478,
            "met": 120,
            "product_name": "hit_product_2",
            "insert_time": "2021-01-02T00:00:00Z",
            "data": "item2",
        },
        {
            "apid": 478,
            "met": 130,
            "product_name": "codicelo_product_1",
            "insert_time": "2021-01-03T00:00:00Z",
            "data": "item3",
        },
        {
            "apid": 478,
            "met": 110,
            "product_name": "mag_product_1",
            "insert_time": "2021-01-04T00:00:00Z",
            "data": "item4",
        },
    ]

    for item in sample_data:
        table.put_item(Item=item)

    return table


def test_query_with_met_range(algorithm_table):
    """Test GET /query?met_start=100&met_end=111."""
    event = {
        "queryStringParameters": {
            "met_start": "100",
            "met_end": "111",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)
    items = json.loads(response["body"])
    met = sorted(item["met"] for item in items)

    expected_data = [str(101), str(110)]

    assert met == expected_data


def test_query_with_met_start(algorithm_table):
    """Test GET /query?met_start=120."""
    event = {
        "queryStringParameters": {
            "met_start": "120",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)
    items = json.loads(response["body"])
    met = sorted(item["met"] for item in items)

    expected_data = [str(120), str(130)]
    assert met == expected_data


def test_query_with_met_end(algorithm_table):
    """Test GET /query?met_end=120."""
    event = {
        "queryStringParameters": {
            "met_end": "120",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)

    assert response["statusCode"] == 400


def test_query_with_insert_time_range(algorithm_table):
    """Test query_with_insert_time_range."""
    # GET /query?insert_time_start=<insert_time_start>&
    # insert_time_end=<insert_time_end>
    event = {
        "queryStringParameters": {
            "insert_time_start": "2021-01-01T00:00:00Z",
            "insert_time_end": "2021-01-03T00:00:00Z",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)
    items = json.loads(response["body"])

    insert_times = sorted(item["insert_time"] for item in items)

    expected_insert_times = [
        "2021-01-01T00:00:00Z",
        "2021-01-02T00:00:00Z",
        "2021-01-03T00:00:00Z",
    ]

    assert insert_times == expected_insert_times


def test_query_with_insert_time_start(algorithm_table):
    """Test GET /query?insert_time_start=<insert_time_start>."""
    event = {
        "queryStringParameters": {
            "insert_time_start": "2021-01-02T00:00:00Z",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)
    items = json.loads(response["body"])

    insert_times = sorted(item["insert_time"] for item in items)

    expected_data = [
        "2021-01-02T00:00:00Z",
        "2021-01-03T00:00:00Z",
        "2021-01-04T00:00:00Z",
    ]

    assert insert_times == expected_data


def test_query_with_insert_time_end(algorithm_table):
    """Test GET /query?insert_time_end=<insert_time_end>."""
    event = {
        "queryStringParameters": {
            "insert_time_end": "2021-01-02T00:00:00Z",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)
    assert response["statusCode"] == 400


def test_query_no_results(algorithm_table):
    """Test GET /query?met_start=<met_start>&met_end=<met_end>."""
    event = {
        "queryStringParameters": {
            "met_start": "200",
            "met_end": "300",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == []


def test_query_with_product_name_prefix(algorithm_table):
    """Test GET /query?product_name=hit*."""
    event = {
        "queryStringParameters": {
            "product_name": "hit*",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)
    items = json.loads(response["body"])
    returned_data = sorted(item["data"] for item in items)
    assert returned_data == ["item1", "item2"]


def test_query_with_product_name(algorithm_table):
    """Test GET /query?product_name=hit*."""
    event = {
        "queryStringParameters": {
            "product_name": "hit_product_1",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)
    items = json.loads(response["body"])
    returned_data = sorted(item["data"] for item in items)
    assert returned_data == ["item1"]


def test_query_with_multiple_filters(algorithm_table):
    """Test GET /query?met_start=100&met_end=130&product_name=codicelo_product_1."""
    event = {
        "queryStringParameters": {
            "met_start": "100",
            "met_end": "130",
            "product_name": "codicelo_product_1",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)

    items = json.loads(response["body"])
    assert items[0]["data"] == "item3"


# TODO!
def test_query_with_multiple_conditions(algorithm_table):
    """Test query API with multiple filters."""
    event = {
        "queryStringParameters": {
            "met_start": "100",
            "met_end": "130",
            "product_name": "hit*",
            "ingest_time": "2021-01*",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)
    assert response["statusCode"] == 200

    items = json.loads(response["body"])
    assert len(items) == 2  # Both `hit_product_1` entries match
    returned_data = sorted(item["data"] for item in items)
    assert returned_data == ["item1", "item2"]
