"""Test the I-ALiRT SSR processing lambda."""

# ruff: noqa: E402
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import imap_data_access
import numpy as np
import pytest
import xarray as xr
from imap_data_access.processing_input import ProcessingInputCollection, SPICEInput

from sds_data_manager.lambda_code.IAlirtCode import ialirt_ssr_utils
from sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_utils import KERNELS

# The Lambda imports ialirt_ssr_utils by its plain name.
sys.modules["ialirt_ssr_utils"] = ialirt_ssr_utils

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing import (
    SOFTWARE_VERSIONS,
    IalirtDataGapError,
    ancillary_item,
    check_day_coverage,
    format_items,
    lambda_handler,
    mag_hk_items,
    process_day,
    process_spacecraft_status,
    spacecraft_items,
    spice_item,
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
    result = [
        {
            "apid": 478,
            "met": 1,
            "met_in_utc": "2026-10-08T12:00:00",
            "instrument": "mag",
            "x": 5,
            "mag_hk_status": {"hk": 1},
        }
    ]

    items = format_items(result, "mag", "v001", "2026-10-19T12:00:00.000001")

    assert items == [
        {
            "instrument_version": "mag#v001",
            "time_utc": "2026-10-08T12:00:00",
            "instrument": "mag",
            "x": 5,
            "kernel_set_key": "2026-10-19T12:00:00.000001",
        }
    ]


def test_mag_hk_items():
    """Test the DynamoDB item for MAG housekeeping."""
    result = [{"met_in_utc": "2026-10-08T12:00:00", "mag_hk_status": {"hk": 1}}]

    items = mag_hk_items(result, "v001", "2026-10-19T12:00:00.000001")

    assert items == [
        {
            "instrument_version": "mag_hk#v001",
            "time_utc": "2026-10-08T12:00:00",
            "instrument": "mag_hk",
            "mag_hk_status": {"hk": 1},
            "kernel_set_key": "2026-10-19T12:00:00.000001",
        }
    ]


def test_spice_item():
    """Test the DynamoDB item for the SPICE kernels used."""
    dependencies = ProcessingInputCollection()
    dependencies.add(
        SPICEInput(
            "naif0012.tls",
            "imap_pred_20260922_20261020_v01.bsp",
            "imap_pred_20261020_20261117_v01.bsp",
        )
    )

    item = spice_item(dependencies, "v001", "2026-10-19T12:00:00.000001", 5)

    # Both ephemeris files are kept, in load order.
    assert item == {
        "instrument_version": "spice#v001",
        "time_utc": "2026-10-19T12:00:00.000001",
        "instrument": "spice",
        "spice_kernels": {
            "leapseconds": ["naif0012.tls"],
            "ephemeris_predicted": [
                "imap_pred_20260922_20261020_v01.bsp",
                "imap_pred_20261020_20261117_v01.bsp",
            ],
        },
        "ttj2000ns": 5,
    }


def test_ancillary_item():
    """Test the DynamoDB item for the ancillary files used."""
    ancillary_files = {"mag_l1d-calibration": "imap_mag_l1d-calibration.cdf"}

    item = ancillary_item(ancillary_files, "v001", "2026-10-19T12:00:00.000001", 5)

    assert item == {
        "instrument_version": "ancillary#v001",
        "time_utc": "2026-10-19T12:00:00.000001",
        "instrument": "ancillary",
        "ancillary_files": ancillary_files,
        "software_versions": SOFTWARE_VERSIONS,
        "ttj2000ns": 5,
    }


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

    items = spacecraft_items(datetime(2026, 10, 8), "v001", "2026-10-19T12:00:00")

    assert len(items) == 1440
    assert items[0]["time_utc"] == "2026-10-08T00:00:00"
    assert items[0]["kernel_set_key"] == "2026-10-19T12:00:00"


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.et_to_ttj2000ns")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.str_to_et")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.process_hit")
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
def test_process_day_empty_product(
    mock_get_kernels,
    mock_download_spice,
    mock_spiceypy,
    mock_query,
    mock_parse,
    mock_check,
    mock_trim,
    mock_process_hit,
    mock_str_to_et,
    mock_et_to_ttj2000ns,
):
    """Test that nothing is written if a product is empty."""
    mock_process_hit.return_value = [{"met_in_utc": "2026-10-08T12:00:00"}, {}]
    table = MagicMock()

    process_day("hit", datetime(2026, 10, 8), "v001", "https://url", table)

    table.batch_writer.assert_not_called()


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.et_to_ttj2000ns")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.str_to_et")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.boto3.client")
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
    mock_ssm_client,
    mock_str_to_et,
    mock_et_to_ttj2000ns,
    setup_ssr_table,
    monkeypatch,
):
    """Test that lambda_handler writes MAG, MAG HK, spice and ancillary items."""
    monkeypatch.setenv("INSTRUMENT", "mag")
    monkeypatch.setenv("SSM_API_KEY_PARAMETER", "/imap-sdc/batch-jobs/api-key")
    # Restore the global imap_data_access config after the test.
    monkeypatch.setitem(imap_data_access.config, "API_KEY", None)
    mock_ssm_client.return_value.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_process_mag.return_value = (
        [{"met_in_utc": "2026-10-08T12:00:00", "mag_hk_status": {"hk": 1}}],
        {"mag_l1b-calibration": "imap_mag_l1b-calibration.cdf"},
    )

    lambda_handler({"day": "20261008", "version": "v001"}, {})

    # MAG uses SPICE geometry, so it gets all kernel types.
    assert mock_get_kernels.call_args.args[2] == KERNELS
    assert imap_data_access.config["API_KEY"] == "test-api-key"
    # Each item is written, and the MAG item points to the spice and
    # ancillary items by kernel_set_key.
    table = setup_ssr_table["ssr_table"]
    mag = table.get_item(
        Key={"instrument_version": "mag#v001", "time_utc": "2026-10-08T12:00:00"}
    )["Item"]
    kernel_set_key = mag["kernel_set_key"]
    for instrument_version, time_utc in [
        ("mag_hk#v001", "2026-10-08T12:00:00"),
        ("spice#v001", kernel_set_key),
        ("ancillary#v001", kernel_set_key),
    ]:
        key = {"instrument_version": instrument_version, "time_utc": time_utc}
        assert "Item" in table.get_item(Key=key)
