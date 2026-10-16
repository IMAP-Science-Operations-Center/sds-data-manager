"""Test the I-ALiRT SSR processing lambda."""

# ruff: noqa: E402
import sys
from datetime import datetime
from unittest.mock import patch

import numpy as np
import pytest
import xarray as xr
from boto3.dynamodb.conditions import Key

from sds_data_manager.lambda_code.IAlirtCode import ialirt_ssr_utils

# The Lambda imports ialirt_ssr_utils by its plain name.
sys.modules["ialirt_ssr_utils"] = ialirt_ssr_utils

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing import (
    IalirtDataGapError,
    check_day_coverage,
    format_items,
    lambda_handler,
    process_spacecraft_status,
    spacecraft_items,
    trim_to_day,
)

DAY_NS = 86400 * 10**9


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.str_yyyymmdd_to_ttj2000ns"
)
def test_check_day_coverage(mock_str_to_ttj2000ns):
    """Test that a complete day passes."""
    mock_str_to_ttj2000ns.side_effect = [0, DAY_NS]
    # A packet every 30 seconds until just after midnight.
    epoch = np.arange(0, 86430, 30) * 10**9
    combined = xr.Dataset(
        {"src_seq_ctr": ("epoch", np.arange(len(epoch)))}, coords={"epoch": epoch}
    )

    check_day_coverage(combined, datetime(2026, 10, 8))


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.ttj2000ns_to_utc")
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.str_yyyymmdd_to_ttj2000ns"
)
def test_check_day_coverage_gap(mock_str_to_ttj2000ns, mock_to_utc):
    """Test that a missing packet raises."""
    mock_str_to_ttj2000ns.side_effect = [0, DAY_NS]
    epoch = np.arange(0, 86430, 30) * 10**9
    src_seq_ctr = np.arange(len(epoch))
    src_seq_ctr[100:] += 1
    combined = xr.Dataset(
        {"src_seq_ctr": ("epoch", src_seq_ctr)}, coords={"epoch": epoch}
    )

    with pytest.raises(IalirtDataGapError):
        check_day_coverage(combined, datetime(2026, 10, 8))


def test_trim_to_day():
    """Test that products outside the day are dropped."""
    result = [
        {"met_in_utc": "2026-10-07T23:59:59"},
        {"met_in_utc": "2026-10-08T12:00:00"},
        {"met_in_utc": "2026-10-09T00:00:00"},
    ]

    assert trim_to_day(result, datetime(2026, 10, 8)) == [
        {"met_in_utc": "2026-10-08T12:00:00"}
    ]


def test_format_items():
    """Test the DynamoDB item for a data product."""
    result = [{"apid": 478, "met": 1, "met_in_utc": "2026-10-08T12:00:00", "x": 5}]

    items = format_items(result, "mag", "v001", ["naif0012.tls"], {})

    assert items == [
        {
            "instrument_version": "mag#v001",
            "time_utc": "2026-10-08T12:00:00",
            "instrument": "mag",
            "x": 5,
            "spice_kernels": ["naif0012.tls"],
            "ancillary_files": {},
        }
    ]


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.process_status")
def test_process_spacecraft_status(mock_process_status):
    """Test that there is one spacecraft status product per minute."""
    mock_process_status.return_value = [{"met_in_utc": "2026-10-08T12:00:00"}]
    # 3 minutes of packets, 1 per second.
    combined = xr.Dataset(coords={"epoch": np.arange(180) * 10**9})

    result = process_spacecraft_status(combined)

    assert len(result) == 3


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.et_to_ttj2000ns")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.str_to_et")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.imap_state")
def test_spacecraft_items(mock_imap_state, mock_str_to_et, mock_et_to_ttj2000ns):
    """Test that there is one spacecraft position item per minute."""
    mock_imap_state.return_value = np.ones((1440, 6))
    mock_str_to_et.return_value = 0.0
    mock_et_to_ttj2000ns.return_value = 0

    items = spacecraft_items(datetime(2026, 10, 8), "v001", ["naif0012.tls"])

    assert len(items) == 1440
    assert items[0]["time_utc"] == "2026-10-08T00:00:00"


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.process_mag")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.trim_packets")
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.check_day_coverage"
)
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.parse_packets")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.query_filenames")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.spiceypy")
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.download_spice_files"
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.get_latest_spice_kernels"
)
def test_lambda_handler(
    mock_get_kernels,
    mock_download_spice,
    mock_spiceypy,
    mock_query,
    mock_parse,
    mock_check,
    mock_trim,
    mock_process_mag,
    setup_ssr_table,
    monkeypatch,
    tmp_path,
):
    """Test that lambda_handler writes MAG items to the SSR table."""
    monkeypatch.setenv("INSTRUMENT", "mag")
    kernel = tmp_path / "naif0012.tls"
    kernel.touch()
    mock_download_spice.return_value = [kernel]
    mock_process_mag.return_value = ([{"met_in_utc": "2026-10-08T12:00:00"}], {})

    lambda_handler({"day": "20261008", "version": "v001"}, {})

    response = setup_ssr_table["ssr_table"].query(
        KeyConditionExpression=Key("instrument_version").eq("mag#v001")
    )
    assert response["Items"][0]["time_utc"] == "2026-10-08T12:00:00"
