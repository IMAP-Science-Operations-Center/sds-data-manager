"""Tests for the I-ALiRT DB Query API Lambda function."""

import importlib
import json
import os
from decimal import Decimal

import pytest


@pytest.fixture
def ialirt_db_query_api_module(setup_dynamodb):
    """Mock the import."""
    os.environ["ALGORITHM_TABLE"] = setup_dynamodb["algorithm_table"].name
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"

    from sds_data_manager.lambda_code.IAlirtCode import ialirt_db_query_api

    importlib.reload(ialirt_db_query_api)
    return ialirt_db_query_api


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
            "data": "item1",
        },
        {
            "apid": 478,
            "met": 120,
            "last_modified": "2021-01-02T00:00:00",
            "met_in_utc": "2021-01-02T00:00:00",
            "data": "item2",
        },
        {
            "apid": 478,
            "met": 130,
            "last_modified": "2021-01-03T00:00:00",
            "met_in_utc": "2021-01-03T00:00:00",
            "data": "item3",
        },
        {
            "apid": 478,
            "met": 110,
            "last_modified": "2021-01-04T00:00:00",
            "met_in_utc": "2021-01-04T00:00:00",
            "data": "item4",
        },
    ]

    for item in sample_data:
        table.put_item(Item=item)

    return table


def test_query_with_met_range(algorithm_table, ialirt_db_query_api_module):
    """Test query with met range."""
    # GET <invoke url>/query?met_start=100&met_end=111
    event = {
        "queryStringParameters": {
            "met_start": "100",
            "met_end": "111",
        }
    }
    response = ialirt_db_query_api_module.lambda_handler(event, context=None)
    items = json.loads(response["body"])
    met = sorted(item["met"] for item in items)

    expected_data = [101, 110]

    assert met == expected_data


def test_query_with_met_start(algorithm_table, ialirt_db_query_api_module):
    """Test query with met start."""
    # GET <invoke url>/query?met_start=120
    event = {
        "queryStringParameters": {
            "met_start": "120",
        }
    }
    response = ialirt_db_query_api_module.lambda_handler(event, context=None)
    items = json.loads(response["body"])
    met = sorted(item["met"] for item in items)

    expected_data = [120, 130]
    assert met == expected_data


def test_query_with_met_end(algorithm_table, ialirt_db_query_api_module):
    """Test query with met end."""
    # GET <invoke url>/query?met_end=120
    event = {
        "queryStringParameters": {
            "met_end": "120",
        }
    }
    response = ialirt_db_query_api_module.lambda_handler(event, context=None)

    assert response["statusCode"] == 400
    expected_message = {"message": "Cannot query by end time without start time"}
    assert json.loads(response["body"]) == expected_message


def test_query_with_utc_range(algorithm_table, ialirt_db_query_api_module):
    """Test query_with_utc_range."""
    # GET <invoke url>/query?met_in_utc_start=<met_in_utc_start>&
    # met_in_utc_end=<met_in_utc_end>
    event = {
        "queryStringParameters": {
            "met_in_utc_start": "2021-01-01T00:00:00",
            "met_in_utc_end": "2021-01-03T00:00:00",
        }
    }
    response = ialirt_db_query_api_module.lambda_handler(event, context=None)
    items = json.loads(response["body"])

    utc = sorted(item["met_in_utc"] for item in items)

    expected_utc = [
        "2021-01-01T00:00:00",
        "2021-01-02T00:00:00",
        "2021-01-03T00:00:00",
    ]

    assert utc == expected_utc


def test_query_with_utc_start(algorithm_table, ialirt_db_query_api_module):
    """Test with insert time start."""
    # GET <invoke url>/query?utc_start=<utc_start>
    event = {
        "queryStringParameters": {
            "met_in_utc_start": "2021-01-02T00:00:00",
        }
    }
    response = ialirt_db_query_api_module.lambda_handler(event, context=None)
    items = json.loads(response["body"])

    utcs = sorted(item["met_in_utc"] for item in items)

    expected_data = [
        "2021-01-02T00:00:00",
        "2021-01-03T00:00:00",
        "2021-01-04T00:00:00",
    ]

    assert utcs == expected_data


def test_query_with_utc_end(algorithm_table, ialirt_db_query_api_module):
    """Test query with insert time end."""
    # GET <invoke url>/query?met_in_utc_end=<met_in_utc_end>
    event = {
        "queryStringParameters": {
            "met_in_utc_end": "2021-01-02T00:00:00",
        }
    }
    response = ialirt_db_query_api_module.lambda_handler(event, context=None)
    assert response["statusCode"] == 400
    expected_message = {"message": "Cannot query by end time without start time"}
    assert json.loads(response["body"]) == expected_message


def test_query_no_results(algorithm_table, ialirt_db_query_api_module):
    """Test query if there are no results."""
    # GET <invoke url>/query?met_start=<met_start>&met_end=<met_end>
    event = {
        "queryStringParameters": {
            "met_start": "200",
            "met_end": "300",
        }
    }
    response = ialirt_db_query_api_module.lambda_handler(event, context=None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == []


def test_query_with_multiple_filters(algorithm_table, ialirt_db_query_api_module):
    """Test query with multiple filters."""
    # GET <invoke url>/query?met_start=100&met_end=130&product_name=codicelo_product_1
    event = {
        "queryStringParameters": {
            "met_start": "100",
            "met_end": "130",
        }
    }
    response = ialirt_db_query_api_module.lambda_handler(event, context=None)

    items = json.loads(response["body"])
    assert len(items) == 4


def test_query_with_different_time_queries(algorithm_table, ialirt_db_query_api_module):
    """Test query API with multiple filters."""
    # GET <invoke url>/query?met_start=100&met_end=130&product_name=hit*&
    # met_in_utc_start=2021-01-02T00:00:00.
    event = {
        "queryStringParameters": {
            "met_start": "100",
            "met_end": "130",
            "met_in_utc_start": "2021-01-02T00:00:00",
        }
    }
    response = ialirt_db_query_api_module.lambda_handler(event, context=None)
    assert response["statusCode"] == 400
    expected_message = {
        "message": "Cannot query multiple time keys (met, met_in_utc, last_modified)"
    }
    assert json.loads(response["body"]) == expected_message


def test_query_with_invalid_parameters(algorithm_table, ialirt_db_query_api_module):
    """Test query with invalid parameters."""
    # GET <invoke url>/query?met_bad=100.
    event = {
        "queryStringParameters": {
            "met_bad": "100",
        }
    }
    response = ialirt_db_query_api_module.lambda_handler(event, context=None)

    assert response["statusCode"] == 400
    expected_message = {"message": "Unexpected parameters: met_bad"}
    assert json.loads(response["body"]) == expected_message


def test_query_with_no_parameters(algorithm_table, ialirt_db_query_api_module):
    """Test query with no parameters."""
    # GET <invoke url>/query.
    event = {"queryStringParameters": None}
    response = ialirt_db_query_api_module.lambda_handler(event, context=None)

    assert response["statusCode"] == 400
    expected_message = {"message": "No query parameters provided"}
    assert json.loads(response["body"]) == expected_message


def test_query_with_mixed_parameters(algorithm_table, ialirt_db_query_api_module):
    """Test query with mixed parameters."""
    # GET <invoke url>/query?met_start=100&met_in_utc_end=2021-01-02T00:00:00.
    event = {
        "queryStringParameters": {
            "met_start": "100",
            "met_in_utc_end": "2021-01-02T00:00:00",
        }
    }
    response = ialirt_db_query_api_module.lambda_handler(event, context=None)

    assert response["statusCode"] == 400
    expected_message = {
        "message": "Cannot query multiple time keys (met, met_in_utc, last_modified)"
    }
    assert json.loads(response["body"]) == expected_message


def test_process_item_types(ialirt_db_query_api_module):
    """Test process_item_types function."""
    items = [
        {
            "apid": Decimal("478"),
            "met": Decimal("123456789"),
            "ttj2000ns": Decimal("123456789000000"),
            "mag_B_GSE": [Decimal("0.0"), Decimal("0.1"), Decimal("0.2")],
            "mag_B_magnitude": Decimal("0.22"),
            "met_in_utc": "2025-06-20T08:00:00",  # string should stay unchanged
            "mag_hk_status": {"pri_isvalid": True, "hkn8v5": Decimal("3680")},
            "codice_hi_h": [
                [
                    [
                        [Decimal("10"), Decimal("12")],
                        [Decimal("11"), Decimal("14")],
                    ],
                ]
            ],
        }
    ]

    # Use JSON encoder to process Decimals instead of process_item_types
    json_output = json.dumps(
        items,
        cls=ialirt_db_query_api_module.DecimalEncoder,
    )
    processed_items = json.loads(json_output)

    assert processed_items == [
        {
            "apid": 478,
            "met": 123456789,
            "ttj2000ns": 123456789000000,
            "mag_B_GSE": [0.0, 0.1, 0.2],
            "mag_B_magnitude": 0.22,
            "met_in_utc": "2025-06-20T08:00:00",
            "mag_hk_status": {"pri_isvalid": True, "hkn8v5": 3680},
            "codice_hi_h": [[[[10, 12], [11, 14]]]],
        }
    ]
