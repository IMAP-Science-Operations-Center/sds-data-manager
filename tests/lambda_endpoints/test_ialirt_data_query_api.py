"""Tests for the I-ALiRT Data Query API Lambda function."""

import importlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from boto3.dynamodb.conditions import Key


@pytest.fixture
def data_table(setup_data_table):
    """Return the mocked table and populate it with sample data."""
    table = setup_data_table["data_table"]
    now = datetime.now(timezone.utc)

    sample_data = [
        {
            "instrument": "mag",
            "time_utc": "2021-01-01T00:00:00",
            "data": "item1",
        },
        {
            "instrument": "mag_hk",
            "time_utc": "2021-01-02T00:00:00",
            "data": "item2",
        },
        {
            "instrument": "hit",
            "time_utc": "2021-01-03T00:00:00",
            "data": "item3",
        },
        {
            "instrument": "spice",
            "time_utc": "2021-01-04T00:00:00",
            "data": "item4",
        },
        {
            "instrument": "mag",
            "time_utc": now.isoformat()[0:19],
            "data": "item4",
        },
    ]

    for item in sample_data:
        table.put_item(Item=item)

    return table


@pytest.fixture
def event():
    """Minimal API Gateway event for testing."""
    return {
        "queryStringParameters": {
            "met_start": "497372400",
            "met_end": "497376000",
            "last_evaluated_key": '{"instrument": "hit",'
            ' "time_utc": "2025-10-01T15:10:01.123456Z"}',
        },
        "headers": {
            "host": "ialirt.imap-mission.com",
            "x-forwarded-proto": "https",
        },
        "requestContext": {"http": {"path": "/api-key/space-weather"}},
    }


@pytest.fixture
def ialirt_data_query_api_module(setup_data_table):
    """Mock the import."""
    os.environ["DATA_TABLE"] = setup_data_table["data_table"].name
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"

    from sds_data_manager.lambda_code.IAlirtCode import ialirt_data_query_api

    importlib.reload(ialirt_data_query_api)
    return ialirt_data_query_api


def test_error_response(ialirt_data_query_api_module):
    """Test that _error() returns the correct structure."""
    response = ialirt_data_query_api_module._error(404, "Not Found")

    assert isinstance(response, dict)
    assert response["statusCode"] == 404
    assert response["headers"] == {"Content-Type": "application/json"}

    body = json.loads(response["body"])
    assert body == {"message": "Not Found"}


def test_apply_time_filters_between(ialirt_data_query_api_module):
    """Test when both start and end times are provided."""
    params = {
        "time_utc_start": "2025-10-01T10:00:00Z",
        "time_utc_end": "2025-10-01T11:00:00Z",
    }
    query_kwargs = {"KeyConditionExpression": Key("instrument").eq("hit")}

    # Call the function
    ialirt_data_query_api_module.apply_time_filters(params, query_kwargs)

    # Get internal structure
    expr = query_kwargs["KeyConditionExpression"]
    parts = expr.get_expression()

    # Check top-level structure (must be AND)
    assert parts["operator"] == "AND"

    equals_expr, between_expr = parts["values"]

    # Check instrument = "hit"
    eq = equals_expr.get_expression()
    assert eq["operator"] == "="
    # Key object is in values[0], actual value is values[1]
    assert eq["values"][1] == "hit"

    # Check time_utc BETWEEN start AND end
    bt = between_expr.get_expression()
    assert bt["operator"] == "BETWEEN"
    assert bt["values"][1] == "2025-10-01T10:00:00"
    assert bt["values"][2] == "2025-10-01T11:00:00"


def test_apply_time_filters_gte(ialirt_data_query_api_module):
    """Test when only start time is provided → gte(time_utc_start)."""
    # Only start time is given → should use Key("time_utc").gte(start)
    params = {"time_utc_start": "2025-10-01T10:00:00Z"}
    query_kwargs = {"KeyConditionExpression": Key("instrument").eq("hit")}

    ialirt_data_query_api_module.apply_time_filters(params, query_kwargs)

    # Inspect the internal structure of the KeyConditionExpression
    expr = query_kwargs["KeyConditionExpression"]
    parts = expr.get_expression()

    # Must be an AND between instrument == 'hit' and time_utc >= start_time
    assert parts["operator"] == "AND"

    equals_expr, bt_expr = parts["values"]

    # --- Check instrument == 'hit'
    eq = equals_expr.get_expression()
    assert eq["operator"] == "="
    assert eq["values"][1] == "hit"

    # --- Check time_utc >= start_time
    bt = bt_expr.get_expression()
    assert bt["operator"] == "BETWEEN"
    assert bt["values"][1] == "2025-10-01T10:00:00"


def test_apply_time_filters_error(ialirt_data_query_api_module):
    """Test when only end time is provided → return error."""
    params = {"time_utc_end": "2025-10-01T11:00:00Z"}
    query_kwargs = {"KeyConditionExpression": Key("instrument").eq("hit")}

    result = ialirt_data_query_api_module.apply_time_filters(params, query_kwargs)

    # This should be an error dict
    assert result[1] == "2025-10-01T10:00:00"


def test_query_with_utc_range(data_table, ialirt_data_query_api_module):
    """Test query_with_utc_range."""
    # GET <invoke url>/query?met_in_utc_start=<met_in_utc_start>&
    # met_in_utc_end=<met_in_utc_end>
    event = {
        "queryStringParameters": {
            "met_in_utc_start": "2021-01-01T00:00:00",
            "met_in_utc_end": "2021-01-03T00:00:00",
        }
    }
    response = ialirt_data_query_api_module.lambda_handler(event, context=None)

    assert response["statusCode"] == 200


def test_query_with_utc_start(data_table, ialirt_data_query_api_module):
    """Test with insert time start."""
    # GET <invoke url>/query?utc_start=<utc_start>
    event = {
        "queryStringParameters": {
            "met_in_utc_start": "2021-01-03T00:00:00",
        }
    }
    response = ialirt_data_query_api_module.lambda_handler(event, context=None)
    items = json.loads(response["body"])

    utc = sorted(data["time_utc"] for data in items["data"])

    assert "2021-01-03T00:00:00" in utc


def test_query_with_utc_end(data_table, ialirt_data_query_api_module):
    """Test query with insert time end."""
    # GET <invoke url>/query?met_in_utc_end=<met_in_utc_end>
    event = {
        "queryStringParameters": {
            "met_in_utc_end": "2021-01-03T00:00:00",
        }
    }
    response = ialirt_data_query_api_module.lambda_handler(event, context=None)
    items = json.loads(response["body"])
    assert items["data"][0]["time_utc"] == "2021-01-03T00:00:00"


def test_query_results(data_table, ialirt_data_query_api_module):
    """Test query if there are no results."""
    # GET <invoke url>/query?met_start=<met_start>&met_end=<met_end>
    event = {
        "queryStringParameters": {
            "met_in_utc_start": "2021-01-05T00:00:00",
        }
    }
    response = ialirt_data_query_api_module.lambda_handler(event, context=None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["meta"]["count"] == 0


def test_query_with_multiple_filters(data_table, ialirt_data_query_api_module):
    """Test query with multiple filters."""
    # GET <invoke url>/query?instrument=mag&met_in_utc_start=2021-01-01T00:00:00
    event = {
        "queryStringParameters": {
            "instrument": "mag",
            "met_in_utc_start": "2021-01-01T00:00:00",
        }
    }
    response = ialirt_data_query_api_module.lambda_handler(event, context=None)

    items = json.loads(response["body"])["data"][0]["data"]
    assert items == "item1"


def test_query_with_different_time_queries(data_table, ialirt_data_query_api_module):
    """Test query API with multiple filters."""
    # GET <invoke url>/query?instrument=hit&time_utc_start=2021-01-02T00:00:00&
    # time_utc_end=2021-01-03T00:00:00&
    # met_in_utc_start=2021-01-02T00:00:00.
    event = {
        "queryStringParameters": {
            "time_utc_start": "2021-01-02T00:00:00",
            "time_utc_end": "2021-01-03T00:00:00",
            "met_in_utc_start": "2021-01-02T00:00:00",
        }
    }
    response = ialirt_data_query_api_module.lambda_handler(event, context=None)
    items = json.loads(response["body"])["data"][0]["data"]
    assert items == "item3"


def test_query_with_invalid_parameters(data_table, ialirt_data_query_api_module):
    """Test query with invalid parameters."""
    # GET <invoke url>/query?met_bad=100.
    event = {
        "queryStringParameters": {
            "met_bad": "100",
        }
    }
    response = ialirt_data_query_api_module.lambda_handler(event, context=None)

    assert response["statusCode"] == 400
    expected_message = {"message": "Unexpected parameters: met_bad"}
    assert json.loads(response["body"]) == expected_message


def test_query_with_no_parameters(data_table, ialirt_data_query_api_module):
    """Test query with no parameters."""
    # GET <invoke url>/query.
    event = {"queryStringParameters": None}
    response = ialirt_data_query_api_module.lambda_handler(event, context=None)

    assert json.loads(response["body"])["data"][0]["instrument"] == "mag"


def test_process_item_types(ialirt_data_query_api_module):
    """Test Decimal handling via JSON encoder."""
    items = [
        {
            "instrument": "mag",
            "time_utc": "2025-06-20T08:00:00",
            "ttj2000ns": Decimal("123456789000000"),
            "mag_B_GSE": [Decimal("0.0"), Decimal("0.1"), Decimal("0.2")],
            "mag_B_magnitude": Decimal("0.22"),
            "mag_hk_status": {"pri_isvalid": True, "hkn8v5": Decimal("3680")},
        }
    ]

    # Use JSON encoder to process Decimals instead of process_item_types
    json_output = json.dumps(
        items,
        cls=ialirt_data_query_api_module.DecimalEncoder,
    )
    processed_items = json.loads(json_output)

    assert processed_items == [
        {
            "instrument": "mag",
            "time_utc": "2025-06-20T08:00:00",
            "ttj2000ns": 123456789000000,
            "mag_B_GSE": [0.0, 0.1, 0.2],
            "mag_B_magnitude": 0.22,
            "mag_hk_status": {"pri_isvalid": True, "hkn8v5": 3680},
        }
    ]
