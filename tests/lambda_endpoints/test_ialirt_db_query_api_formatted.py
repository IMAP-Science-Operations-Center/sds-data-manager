"""Tests for the I-ALiRT DB Query API Lambda function."""

import json
from decimal import Decimal

import pytest

from sds_data_manager.lambda_code.IAlirtCode import ialirt_db_query_api_formatted


@pytest.fixture
def algorithm_table(setup_dynamodb):
    """Return the mocked imap-algorithm-table and populate it with sample data."""
    table = setup_dynamodb["algorithm_table"]

    sample_data = [
        {
            "apid": 478,
            "met": 101,
            "last_modified": "2021-01-01T00:00:00",
            "met_in_utc": "2021-01-01T00:00:00",
            "codice_hi_data": "item1",
        },
        {
            "apid": 478,
            "met": 120,
            "last_modified": "2021-01-02T00:00:00",
            "met_in_utc": "2021-01-02T00:00:00",
            "codice_hi_data": "item2",
        },
        {
            "apid": 478,
            "met": 130,
            "last_modified": "2021-01-03T00:00:00",
            "met_in_utc": "2021-01-03T00:00:00",
            "mag_data_product": "item3",
        },
        {
            "apid": 478,
            "met": 110,
            "last_modified": "2021-01-04T00:00:00",
            "met_in_utc": "2021-01-04T00:00:00",
            "mag_data_product": "item4",
        },
    ]

    for item in sample_data:
        table.put_item(Item=item)

    return table


def test_query_with_met_range(algorithm_table):
    """Test query with met range."""
    # GET <invoke url>/query?time_start=100&time_end=111
    event = {
        "queryStringParameters": {
            "time_start": "100",
            "time_end": "111",
        }
    }
    response = ialirt_db_query_api_formatted.lambda_handler(event, context=None)
    items = json.loads(response["body"])

    expected_data = ["2021-01-01T00:00:00", "2021-01-04T00:00:00"]

    assert items["time_tag_utc"] == expected_data


def test_query_with_met_start(algorithm_table):
    """Test query with met start."""
    # GET <invoke url>/query?time_start=120
    event = {
        "queryStringParameters": {
            "time_start": "120",
        }
    }
    response = ialirt_db_query_api_formatted.lambda_handler(event, context=None)
    items = json.loads(response["body"])

    expected_data = ["2021-01-02T00:00:00", "2021-01-03T00:00:00"]
    assert items["time_tag_utc"] == expected_data


def test_query_with_met_end(algorithm_table):
    """Test query with met end."""
    # GET <invoke url>/query?met_end=120
    event = {
        "queryStringParameters": {
            "time_end": "120",
        }
    }
    response = ialirt_db_query_api_formatted.lambda_handler(event, context=None)

    assert response["statusCode"] == 400
    expected_message = {"message": "Cannot query by end time without start time"}
    assert json.loads(response["body"]) == expected_message


def test_query_with_utc_range(algorithm_table):
    """Test query_with_utc_range."""
    # GET <invoke url>/query?utc_start=<utc_start>&
    # utc_end=<utc_end>
    event = {
        "queryStringParameters": {
            "utc_start": "2021-01-01T00:00:00",
            "utc_end": "2021-01-03T00:00:00",
        }
    }
    response = ialirt_db_query_api_formatted.lambda_handler(event, context=None)
    items = json.loads(response["body"])

    expected_utc = [
        "2021-01-01T00:00:00",
        "2021-01-02T00:00:00",
        "2021-01-03T00:00:00",
    ]

    assert items["time_tag_utc"] == expected_utc


def test_query_with_utc_start(algorithm_table):
    """Test with insert time start."""
    # GET <invoke url>/query?utc_start=<utc_start>
    event = {
        "queryStringParameters": {
            "utc_start": "2021-01-02T00:00:00",
        }
    }
    response = ialirt_db_query_api_formatted.lambda_handler(event, context=None)
    items = json.loads(response["body"])

    expected_data = [
        "2021-01-02T00:00:00",
        "2021-01-03T00:00:00",
        "2021-01-04T00:00:00",
    ]

    assert items["time_tag_utc"] == expected_data


def test_query_with_utc_end(algorithm_table):
    """Test query with insert time end."""
    # GET <invoke url>/query?utc_end=<utc_end>
    event = {
        "queryStringParameters": {
            "utc_end": "2021-01-02T00:00:00",
        }
    }
    response = ialirt_db_query_api_formatted.lambda_handler(event, context=None)
    assert response["statusCode"] == 400
    expected_message = {"message": "Cannot query by end time without start time"}
    assert json.loads(response["body"]) == expected_message


def test_query_no_results(algorithm_table):
    """Test query if there are no results."""
    # GET <invoke url>/query?time_start=<time_start>&met_end=<met_end>
    event = {
        "queryStringParameters": {
            "time_start": "200",
            "time_end": "300",
        }
    }
    response = ialirt_db_query_api_formatted.lambda_handler(event, context=None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {}


def test_query_with_multiple_filters(algorithm_table):
    """Test query with multiple filters."""
    # GET <invoke url>/query?time_start=100&time_end=130&product_name=codicelo_product_1
    event = {
        "queryStringParameters": {
            "time_start": "100",
            "time_end": "130",
        }
    }
    response = ialirt_db_query_api_formatted.lambda_handler(event, context=None)

    items = json.loads(response["body"])
    assert len(items["data"]) == 4


def test_query_with_different_time_queries(algorithm_table):
    """Test query API with multiple filters."""
    # GET <invoke url>/query?time_start=100&time_end=130&product_name=hit*&
    # utc_start=2021-01-02T00:00:00.
    event = {
        "queryStringParameters": {
            "time_start": "100",
            "time_end": "130",
            "utc_start": "2021-01-02T00:00:00",
        }
    }
    response = ialirt_db_query_api_formatted.lambda_handler(event, context=None)
    assert response["statusCode"] == 400
    expected_message = {
        "message": "Cannot query multiple time keys (time, utc, last_modified)"
    }
    assert json.loads(response["body"]) == expected_message


def test_query_with_invalid_parameters(algorithm_table):
    """Test query with invalid parameters."""
    # GET <invoke url>/query?met_bad=100.
    event = {
        "queryStringParameters": {
            "met_bad": "100",
        }
    }
    response = ialirt_db_query_api_formatted.lambda_handler(event, context=None)

    assert response["statusCode"] == 400
    expected_message = {"message": "Unexpected parameters: met_bad"}
    assert json.loads(response["body"]) == expected_message


def test_query_with_no_parameters(algorithm_table):
    """Test query with no parameters."""
    # GET <invoke url>/query.
    event = {"queryStringParameters": None}
    response = ialirt_db_query_api_formatted.lambda_handler(event, context=None)

    assert response["statusCode"] == 400
    expected_message = {"message": "No query parameters provided"}
    assert json.loads(response["body"]) == expected_message


def test_query_with_mixed_parameters(algorithm_table):
    """Test query with mixed parameters."""
    # GET <invoke url>/query?time_start=100&utc_end=2021-01-02T00:00:00.
    event = {
        "queryStringParameters": {
            "time_start": "100",
            "utc_end": "2021-01-02T00:00:00",
        }
    }
    response = ialirt_db_query_api_formatted.lambda_handler(event, context=None)

    assert response["statusCode"] == 400
    expected_message = {
        "message": "Cannot query multiple time keys (time, utc, last_modified)"
    }
    assert json.loads(response["body"]) == expected_message


def test_process_item_types():
    """Test process_item_types function."""
    items = [
        {
            "apid": Decimal("478"),
            "met": Decimal("123456789"),
            "ttj2000ns": Decimal("123456789000000"),
            "mag_B_GSE": [Decimal("0.0"), Decimal("0.1"), Decimal("0.2")],
            "mag_B_magnitude": Decimal("0.22"),
            "met_in_utc": "2025-06-20T08:00:00",  # string should stay unchanged
        }
    ]

    processed_items = [
        ialirt_db_query_api_formatted.process_item_types(item) for item in items
    ]

    assert processed_items == [
        {
            "apid": 478,
            "met": 123456789,
            "ttj2000ns": 123456789000000,
            "mag_B_GSE": [0.0, 0.1, 0.2],
            "mag_B_magnitude": 0.22,
            "met_in_utc": "2025-06-20T08:00:00",
        }
    ]


def test_last_evaluated():
    """Test last evaluated response."""
    last_evaluated_key = {"apid": {"N": "478"}, "met": {"N": "497034344"}}

    processed_item = ialirt_db_query_api_formatted.process_item_types(
        last_evaluated_key
    )
    assert processed_item == {
        "apid": 478,
        "met": 497034344,
    }
