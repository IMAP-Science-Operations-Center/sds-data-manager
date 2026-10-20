"""Tests for the I-ALiRT SSR Data Query API Lambda function."""

import importlib
import json
import os
from decimal import Decimal
from unittest import mock

import pytest


@pytest.fixture
def ssr_table(setup_ssr_table):
    """Return the mocked table and populate it with sample data."""
    table = setup_ssr_table["ssr_table"]

    sample_data = [
        {
            "instrument_version": "mag#v001",
            "time_utc": "2026-09-01T00:00:00",
            "instrument": "mag",
            "data": "mag_early",
        },
        {
            "instrument_version": "mag#v001",
            "time_utc": "2026-09-01T10:00:00",
            "instrument": "mag",
            "data": "mag_mid",
        },
        {
            "instrument_version": "mag#v001",
            "time_utc": "2026-09-01T10:30:00.500",
            "instrument": "mag",
            "data": "mag_latest",
            "value": Decimal("1.23456"),
        },
        {
            "instrument_version": "mag#v002",
            "time_utc": "2026-09-01T10:30:00",
            "instrument": "mag",
            "data": "mag_v002",
        },
        {
            "instrument_version": "hit#v001",
            "time_utc": "2026-09-01T05:00:00",
            "instrument": "hit",
            "hit_e_a_side_high_en": Decimal("1"),
            "hit_other": Decimal("2"),
        },
        {
            "instrument_version": "mag#v001",
            "time_utc": "2026-01-15T00:00:00",
            "instrument": "mag",
            "data": "mag_pre_cutoff",
        },
        {
            "instrument_version": "mag_hk#v001",
            "time_utc": "2026-09-01T10:00:00",
            "instrument": "mag_hk",
            "mag_hk_status": {"hk": 1},
        },
        {
            "instrument_version": "ancillary#v001",
            "time_utc": "2026-10-20T06:00:01.123456",
            "instrument": "ancillary",
            "ancillary_files": {"mag_l1b-calibration": "imap_mag_l1b-calibration.cdf"},
        },
        {
            "instrument_version": "spice#v001",
            "time_utc": "2026-10-20T06:00:01.123456",
            "instrument": "spice",
            "spice_kernels": {"leapseconds": "naif0012.tls"},
        },
        {
            "instrument_version": "spacecraft_status#v001",
            "time_utc": "2026-09-01T00:00:00",
            "instrument": "spacecraft_status",
            "data": "status",
        },
    ]

    for item in sample_data:
        table.put_item(Item=item)

    return table


@pytest.fixture
def ssr_query_api_module(ssr_table):
    """Mock the import."""
    os.environ["SSR_TABLE"] = ssr_table.name
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"

    from sds_data_manager.lambda_code.IAlirtCode import ialirt_ssr_query_api

    importlib.reload(ialirt_ssr_query_api)
    return ialirt_ssr_query_api


def make_event(params=None, scope=None):
    """Create an API Gateway event, with an authorizer if scope is given."""
    event = {"queryStringParameters": params}
    if scope is not None:
        event["requestContext"] = {"authorizer": {"lambda": {"scope": scope}}}
    return event


def call(module, params=None, scope=None):
    """Call the handler and return the status code and parsed body."""
    response = module.lambda_handler(make_event(params, scope), None)
    return response["statusCode"], json.loads(response["body"])


def test_default_version_and_explicit_range(ssr_query_api_module):
    """Test the default version is v001 and the time range is applied."""
    status, body = call(
        ssr_query_api_module,
        {
            "instrument": "mag",
            "time_utc_start": "2026-09-01T09:00:00",
            "time_utc_end": "2026-09-01T11:00:00",
        },
    )

    assert status == 200
    assert body["meta"] == {
        "count": 2,
        "type": "science",
        "instrument": "mag",
        "version": "v001",
    }
    assert [item["data"] for item in body["data"]] == ["mag_mid", "mag_latest"]
    assert body["data"][1]["value"] == 1.235


def test_explicit_version(ssr_query_api_module):
    """Test querying a non-default version."""
    status, body = call(
        ssr_query_api_module,
        {
            "instrument": "mag",
            "version": "v002",
            "time_utc_start": "2026-09-01T10:00:00",
        },
    )

    assert status == 200
    assert body["meta"]["version"] == "v002"
    assert [item["data"] for item in body["data"]] == ["mag_v002"]


def test_start_only(ssr_query_api_module):
    """Test start only returns the hour after start."""
    status, body = call(
        ssr_query_api_module,
        {"instrument": "mag", "time_utc_start": "2026-09-01T09:30:00"},
    )

    assert status == 200
    assert [item["data"] for item in body["data"]] == ["mag_mid"]


def test_end_only(ssr_query_api_module):
    """Test end only returns the hour before end."""
    status, body = call(
        ssr_query_api_module,
        {"instrument": "mag", "time_utc_end": "2026-09-01T10:15:00"},
    )

    assert status == 200
    assert [item["data"] for item in body["data"]] == ["mag_mid"]


def test_latest_time_utc_query(ssr_query_api_module):
    """Test latest_time_utc asks DynamoDB for the newest item only."""
    with mock.patch.object(ssr_query_api_module, "table") as mock_table:
        mock_table.query.return_value = {"Items": [{"time_utc": "2026-09-01"}]}
        assert ssr_query_api_module.latest_time_utc("mag#v001") == "2026-09-01"
        kwargs = mock_table.query.call_args.kwargs
        assert kwargs["ScanIndexForward"] is False
        assert kwargs["Limit"] == 1

        mock_table.query.return_value = {"Items": []}
        assert ssr_query_api_module.latest_time_utc("mag#v001") is None


# moto applies Limit before sorting, so a descending Limit=1 query does not
# return the newest item. Patch latest_time_utc to return the newest item.
LATEST = {
    "mag#v001": "2026-09-01T10:30:00.500",
    "hit#v001": "2026-09-01T05:00:00",
}


@pytest.fixture
def mock_latest(ssr_query_api_module):
    """Patch latest_time_utc with the newest time_utc in the sample data."""
    with mock.patch.object(
        ssr_query_api_module, "latest_time_utc", side_effect=LATEST.get
    ):
        yield


def test_default_latest_hour(ssr_query_api_module, mock_latest):
    """Test no time range returns the latest hour of data."""
    status, body = call(ssr_query_api_module, {"instrument": "mag"})

    assert status == 200
    # Latest item has fractional seconds and must still be included.
    assert [item["data"] for item in body["data"]] == ["mag_mid", "mag_latest"]


def test_default_latest_hour_keeps_microseconds(ssr_query_api_module, mock_latest):
    """Test the default range is exactly 1 hour when time_utc has microseconds."""
    time_range = ssr_query_api_module.get_time_range({}, "mag#v001", True)

    assert time_range == ("2026-09-01T09:30:00.500000", "2026-09-01T10:30:00.500")


def test_default_all_instruments(ssr_query_api_module, mock_latest):
    """Test no instrument returns the latest hour for each science instrument."""
    status, body = call(ssr_query_api_module)

    assert status == 200
    assert body["meta"]["instrument"] == "all"
    data = {item.get("data") for item in body["data"]}
    assert data == {"mag_mid", "mag_latest", None}
    # HIT restricted fields are removed without an API key.
    hit_item = next(item for item in body["data"] if item["instrument"] == "hit")
    assert "hit_e_a_side_high_en" not in hit_item
    assert hit_item["hit_other"] == 2


def test_empty_partition(ssr_query_api_module, mock_latest):
    """Test an instrument with no data returns an empty list."""
    status, body = call(ssr_query_api_module, {"instrument": "swe"})

    assert status == 200
    assert body["meta"]["count"] == 0
    assert body["data"] == []


def test_public_cutoff(ssr_query_api_module):
    """Test data before the public cutoff requires an API key."""
    params = {"instrument": "mag", "time_utc_start": "2026-01-15T00:00:00"}

    status, body = call(ssr_query_api_module, params)
    assert status == 400
    assert "API key required" in body["message"]

    status, body = call(ssr_query_api_module, params, scope="read")
    assert status == 200
    assert [item["data"] for item in body["data"]] == ["mag_pre_cutoff"]


def test_hit_full_scope(ssr_query_api_module):
    """Test HIT restricted fields are returned for full scope."""
    status, body = call(
        ssr_query_api_module,
        {"instrument": "hit", "time_utc_start": "2026-09-01T05:00:00"},
        scope="full",
    )

    assert status == 200
    assert body["data"][0]["hit_e_a_side_high_en"] == 1


def test_spacecraft_status_requires_api_key(ssr_query_api_module):
    """Test spacecraft_status requires an API key."""
    params = {
        "instrument": "spacecraft_status",
        "time_utc_start": "2026-09-01T00:00:00",
    }

    status, _ = call(ssr_query_api_module, params)
    assert status == 403

    status, body = call(ssr_query_api_module, params, scope="")
    assert status == 200
    assert body["meta"]["type"] == "spacecraft_status"
    assert [item["data"] for item in body["data"]] == ["status"]


def test_mag_hk_requires_full_scope(ssr_query_api_module):
    """Test mag_hk requires a full scope API key."""
    params = {"instrument": "mag_hk", "time_utc_start": "2026-09-01T10:00:00"}

    status, _ = call(ssr_query_api_module, params)
    assert status == 403

    status, body = call(ssr_query_api_module, params, scope="full")
    assert status == 200
    assert body["meta"]["type"] == "hk"
    assert body["data"][0]["mag_hk_status"] == {"hk": 1}


def test_spice_by_kernel_set_key(ssr_query_api_module):
    """Test the spice item for a data item's kernel_set_key is returned."""
    kernel_set_key = "2026-10-20T06:00:01.123456"
    status, body = call(
        ssr_query_api_module,
        {
            "instrument": "spice",
            "time_utc_start": kernel_set_key,
            "time_utc_end": kernel_set_key,
        },
    )

    assert status == 200
    assert body["meta"]["type"] == "spice"
    assert body["data"][0]["spice_kernels"] == {"leapseconds": "naif0012.tls"}


def test_spice_by_kernel_set_key_zero_microseconds(ssr_query_api_module):
    """Test a kernel_set_key with .000000 microseconds is still found."""
    kernel_set_key = "2026-10-21T06:00:02.000000"
    ssr_query_api_module.table.put_item(
        Item={
            "instrument_version": "spice#v001",
            "time_utc": kernel_set_key,
            "instrument": "spice",
        }
    )

    status, body = call(
        ssr_query_api_module,
        {
            "instrument": "spice",
            "time_utc_start": kernel_set_key,
            "time_utc_end": kernel_set_key,
        },
    )

    assert status == 200
    assert body["meta"]["count"] == 1


def test_ancillary_by_kernel_set_key(ssr_query_api_module):
    """Test the ancillary item for a data item's kernel_set_key is returned."""
    kernel_set_key = "2026-10-20T06:00:01.123456"
    status, body = call(
        ssr_query_api_module,
        {
            "instrument": "ancillary",
            "time_utc_start": kernel_set_key,
            "time_utc_end": kernel_set_key,
        },
    )

    assert status == 200
    assert body["meta"]["type"] == "science"
    assert body["data"][0]["ancillary_files"] == {
        "mag_l1b-calibration": "imap_mag_l1b-calibration.cdf"
    }


@pytest.mark.parametrize("instrument", ["swe_hk", "foo"])
def test_unsupported_instrument(ssr_query_api_module, instrument):
    """Test instruments not in the SSR table are rejected."""
    status, body = call(ssr_query_api_module, {"instrument": instrument})

    assert status == 400
    assert "Unsupported instrument" in body["message"]


def test_unexpected_params(ssr_query_api_module):
    """Test unexpected parameters are rejected."""
    status, body = call(ssr_query_api_module, {"met_in_utc_start": "x"})

    assert status == 400
    assert "Unexpected parameters" in body["message"]


def test_invalid_time(ssr_query_api_module):
    """Test an invalid time format is rejected."""
    status, body = call(
        ssr_query_api_module, {"instrument": "mag", "time_utc_start": "bad"}
    )

    assert status == 400
    assert body["message"] == "Invalid time format"
