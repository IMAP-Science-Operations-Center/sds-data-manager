"""Test the I-ALiRT SSR processing lambda."""

# ruff: noqa: E402
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import DEFAULT, MagicMock, patch

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
    BOUNDARY_BUFFER,
    COVERAGE_TOLERANCE,
    SOFTWARE_VERSIONS,
    IalirtDataGapError,
    ancillary_item,
    check_day_coverage,
    format_items,
    lambda_handler,
    mag_hk_items,
    process_codice_hi,
    process_codice_lo,
    process_day,
    process_mag,
    process_spacecraft_status,
    process_swapi,
    process_swe_ssr,
    spacecraft_items,
    spice_item,
    trim_packets,
    trim_to_day,
    ttj2000ns_to_utc,
)

MODULE = "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing"
DAY_NS = 86400 * 10**9
# A packet every 30 seconds over the day plus 10 minutes on each side.
FULL_RANGE_S = np.arange(-600, 87001, 30)


def test_coverage_tolerance_less_than_buffer():
    """Test that a gap at the start or end of the day can be caught."""
    assert COVERAGE_TOLERANCE < BOUNDARY_BUFFER


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_processing.str_yyyymmdd_to_ttj2000ns"
)
def test_check_day_coverage(mock_str_to_ttj2000ns):
    """Test that a complete day passes."""
    mock_str_to_ttj2000ns.side_effect = [0, DAY_NS]
    epoch = FULL_RANGE_S * 10**9
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
    epoch = FULL_RANGE_S * 10**9
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


@patch(f"{MODULE}.et_to_utc")
@patch(f"{MODULE}.ttj2000ns_to_et")
def test_ttj2000ns_to_utc(mock_ttj2000ns_to_et, mock_et_to_utc):
    """Test that the epoch is converted to ET and then to UTC."""
    mock_ttj2000ns_to_et.return_value = 1.0
    mock_et_to_utc.return_value = "2026-10-08T00:00:00"

    assert ttj2000ns_to_utc(5) == "2026-10-08T00:00:00"
    mock_ttj2000ns_to_et.assert_called_once_with(5)
    mock_et_to_utc.assert_called_once_with(1.0)


@pytest.mark.parametrize(
    ("epoch_s", "match"),
    [
        # No packets in the day.
        (np.array([], dtype=np.int64), "no packets"),
        # Packets start 8 minutes before midnight, not 10.
        (np.arange(-480, 87001, 30), "first packet"),
        # Packets end 8 minutes after the next midnight, not 10.
        (np.arange(-600, 86880, 30), "last packet"),
    ],
)
@patch(f"{MODULE}.ttj2000ns_to_utc")
@patch(f"{MODULE}.str_yyyymmdd_to_ttj2000ns")
def test_check_day_coverage_incomplete_day(
    mock_str_to_ttj2000ns, mock_to_utc, epoch_s, match
):
    """Test that a day missing its start or end raises."""
    mock_str_to_ttj2000ns.side_effect = [0, DAY_NS]
    epoch = epoch_s * 10**9
    combined = xr.Dataset(
        {"src_seq_ctr": ("epoch", np.arange(len(epoch)))}, coords={"epoch": epoch}
    )

    with pytest.raises(IalirtDataGapError, match=match):
        check_day_coverage(combined, datetime(2026, 10, 8))


@patch(f"{MODULE}.ttj2000ns_to_utc")
@patch(f"{MODULE}.str_yyyymmdd_to_ttj2000ns")
def test_check_day_coverage_missing_start(mock_str_to_ttj2000ns, mock_to_utc):
    """Test that a packet missing at midnight raises."""
    mock_str_to_ttj2000ns.side_effect = [0, DAY_NS]
    # The packet at midnight is missing.
    keep = FULL_RANGE_S != 0
    epoch = FULL_RANGE_S[keep] * 10**9
    src_seq_ctr = np.arange(len(FULL_RANGE_S))[keep]
    combined = xr.Dataset(
        {"src_seq_ctr": ("epoch", src_seq_ctr)}, coords={"epoch": epoch}
    )

    with pytest.raises(IalirtDataGapError, match="src_seq_ctr gap"):
        check_day_coverage(combined, datetime(2026, 10, 8))


@patch(f"{MODULE}.ttj2000ns_to_utc")
@patch(f"{MODULE}.str_yyyymmdd_to_ttj2000ns")
def test_check_day_coverage_time_gap(mock_str_to_ttj2000ns, mock_to_utc):
    """Test that a time gap raises even when src_seq_ctr has no gap."""
    mock_str_to_ttj2000ns.side_effect = [0, DAY_NS]
    # No packets for an hour, but src_seq_ctr keeps counting by one.
    epoch_s = FULL_RANGE_S[(FULL_RANGE_S < 3600) | (FULL_RANGE_S >= 7200)]
    combined = xr.Dataset(
        {"src_seq_ctr": ("epoch", np.arange(len(epoch_s)))},
        coords={"epoch": epoch_s * 10**9},
    )

    with pytest.raises(IalirtDataGapError, match="time gap"):
        check_day_coverage(combined, datetime(2026, 10, 8))


@patch(f"{MODULE}.str_yyyymmdd_to_ttj2000ns")
def test_trim_packets(mock_str_to_ttj2000ns):
    """Test that only packets within 10 minutes of the day are kept."""
    mock_str_to_ttj2000ns.side_effect = [0, DAY_NS]
    minute = 60 * 10**9
    epoch = np.array([-20, -10, 0, 1440, 1450, 1460]) * minute
    combined = xr.Dataset(coords={"epoch": epoch})

    result = trim_packets(combined, datetime(2026, 10, 8))

    np.testing.assert_array_equal(
        result["epoch"].values, np.array([-10, 0, 1440, 1450]) * minute
    )


def mock_get_ancillary(instrument, descriptor, day, download_dir):
    """Return a fake ancillary path named after the descriptor."""
    return Path(f"imap_{instrument}_{descriptor}.cdf")


def mock_mag_products(packets, l1b_cal, ialirt_cal):
    """Return a fake MAG product for every packet in a chunk."""
    return [{"ttj2000ns": int(e)} for e in packets["epoch"].values]


@patch(f"{MODULE}.get_ancillary", side_effect=mock_get_ancillary)
@patch(f"{MODULE}.load_cdf")
@patch(f"{MODULE}.process_packet")
def test_process_mag(mock_process_packet, mock_load_cdf, mock_ancillary):
    """Test that MAG is processed in chunks with the calibration files for the day."""
    mock_process_packet.side_effect = mock_mag_products
    day = datetime(2026, 10, 8)
    # 30 minutes of packets, 1 per second.
    epoch = np.arange(1800) * 10**9
    combined = xr.Dataset(coords={"epoch": epoch})

    result, ancillary_files = process_mag(combined, day, Path("/tmp"))  # noqa: S108

    # Five 7 minute chunks, and each product is kept exactly once.
    assert mock_process_packet.call_count == 5
    assert [p["ttj2000ns"] for p in result] == list(epoch)
    assert ancillary_files == {
        "mag_ialirt-calibration": "imap_mag_ialirt-calibration.cdf",
        "mag_l1b-calibration": "imap_mag_l1b-calibration.cdf",
    }
    assert all(call.args[2] == day for call in mock_ancillary.call_args_list)


@patch(f"{MODULE}.get_ancillary", side_effect=mock_get_ancillary)
@patch(f"{MODULE}.load_cdf")
@patch(f"{MODULE}.process_packet")
def test_process_mag_skips_empty_chunk(
    mock_process_packet, mock_load_cdf, mock_ancillary
):
    """Test that a chunk with no packets is skipped."""
    mock_process_packet.side_effect = mock_mag_products
    # 10 minutes of packets, a 30 minute gap, then 10 more minutes.
    epoch = np.concatenate([np.arange(600), np.arange(2400, 3000)]) * 10**9
    combined = xr.Dataset(coords={"epoch": epoch})

    result, _ = process_mag(combined, datetime(2026, 10, 8), Path("/tmp"))  # noqa: S108

    # The three chunks in the middle of the gap have no packets.
    assert mock_process_packet.call_count == 5
    assert [p["ttj2000ns"] for p in result] == list(epoch)


@patch(f"{MODULE}.get_ancillary", side_effect=mock_get_ancillary)
@patch(f"{MODULE}.process_swe")
def test_process_swe_ssr(mock_process_swe, mock_ancillary):
    """Test that SWE uses the calibration file for the day."""
    mock_process_swe.return_value = [{"met_in_utc": "2026-10-08T12:00:00"}]

    result, ancillary_files = process_swe_ssr(
        MagicMock(),
        datetime(2026, 10, 8),
        Path("/tmp"),  # noqa: S108
    )

    assert result == mock_process_swe.return_value
    assert ancillary_files == {
        "swe_l1b-in-flight-cal": "imap_swe_l1b-in-flight-cal.cdf"
    }
    assert mock_process_swe.call_args.args[1] == [
        Path("imap_swe_l1b-in-flight-cal.cdf")
    ]


@patch(f"{MODULE}.get_ancillary", side_effect=mock_get_ancillary)
@patch(f"{MODULE}.process_codice")
def test_process_codice_lo(mock_process_codice, mock_ancillary):
    """Test that CoDICE Lo returns the Lo products and its ancillary files."""
    lo_result = [{"met_in_utc": "2026-10-08T12:00:00"}]
    mock_process_codice.return_value = (lo_result, [])

    result, ancillary_files = process_codice_lo(
        MagicMock(),
        datetime(2026, 10, 8),
        Path("/tmp"),  # noqa: S108
    )

    assert result == lo_result
    assert ancillary_files == {
        "codice_l1a-sci-lut": "imap_codice_l1a-sci-lut.cdf",
        "codice_l2-lo-efficiency": "imap_codice_l2-lo-efficiency.cdf",
        "codice_l2-lo-gfactor": "imap_codice_l2-lo-gfactor.cdf",
    }
    assert mock_process_codice.call_args.args[3] == "codice_lo"


@patch(f"{MODULE}.get_ancillary", side_effect=mock_get_ancillary)
@patch(f"{MODULE}.process_codice")
def test_process_codice_hi(mock_process_codice, mock_ancillary):
    """Test that CoDICE Hi returns the Hi products and its ancillary files."""
    hi_result = [{"met_in_utc": "2026-10-08T12:00:00"}]
    mock_process_codice.return_value = ([], hi_result)

    result, ancillary_files = process_codice_hi(
        MagicMock(),
        datetime(2026, 10, 8),
        Path("/tmp"),  # noqa: S108
    )

    assert result == hi_result
    assert ancillary_files == {
        "codice_l1a-sci-lut": "imap_codice_l1a-sci-lut.cdf",
        "codice_l2-hi-ialirt-efficiency": "imap_codice_l2-hi-ialirt-efficiency.cdf",
    }
    assert mock_process_codice.call_args.args[3] == "codice_hi"


@patch(f"{MODULE}.get_ancillary", side_effect=mock_get_ancillary)
@patch(f"{MODULE}.pd.read_csv")
@patch(f"{MODULE}.process_swapi_ialirt")
def test_process_swapi(mock_process_swapi, mock_read_csv, mock_ancillary):
    """Test that SWAPI uses the unit conversion file for the day."""
    mock_process_swapi.return_value = [{"met_in_utc": "2026-10-08T12:00:00"}]

    result, ancillary_files = process_swapi(
        MagicMock(),
        datetime(2026, 10, 8),
        Path("/tmp"),  # noqa: S108
    )

    assert result == mock_process_swapi.return_value
    assert ancillary_files == {
        "swapi_esa-unit-conversion": "imap_swapi_esa-unit-conversion.cdf"
    }
    mock_read_csv.assert_called_once_with(Path("imap_swapi_esa-unit-conversion.cdf"))


@pytest.fixture
def mock_process_day_inputs():
    """Mock SPICE, packet download and coverage checks for process_day."""
    with patch.multiple(
        MODULE,
        get_latest_spice_kernels=DEFAULT,
        download_spice_files=DEFAULT,
        spiceypy=DEFAULT,
        query_filenames=DEFAULT,
        parse_packets=DEFAULT,
        check_day_coverage=DEFAULT,
        trim_packets=DEFAULT,
        str_to_et=DEFAULT,
        et_to_ttj2000ns=DEFAULT,
    ) as mocks:
        yield mocks


@pytest.mark.parametrize(
    ("instrument", "function_name", "ancillary_files"),
    [
        ("mag", "process_mag", {"mag_l1b-calibration": "mag.cdf"}),
        ("hit", "process_hit", None),
        ("swe", "process_swe_ssr", {"swe_l1b-in-flight-cal": "swe.cdf"}),
        ("codice_lo", "process_codice_lo", {"codice_l1a-sci-lut": "lut.cdf"}),
        ("codice_hi", "process_codice_hi", {"codice_l1a-sci-lut": "lut.cdf"}),
        ("swapi", "process_swapi", {"swapi_esa-unit-conversion": "swapi.csv"}),
        ("spacecraft_status", "process_spacecraft_status", None),
    ],
)
def test_process_day_instruments(
    mock_process_day_inputs, instrument, function_name, ancillary_files
):
    """Test that each instrument's products and ancillary item are written."""
    result = [{"met_in_utc": "2026-10-08T12:00:00", "mag_hk_status": {"hk": 1}}]
    # HIT and spacecraft status have no ancillary files.
    return_value = result if ancillary_files is None else (result, ancillary_files)
    table = MagicMock()

    with patch(f"{MODULE}.{function_name}", return_value=return_value):
        process_day(instrument, datetime(2026, 10, 8), "v001", "https://url", table)

    batch = table.batch_writer.return_value.__enter__.return_value
    items = {
        call.kwargs["Item"]["instrument_version"]: call.kwargs["Item"]
        for call in batch.put_item.call_args_list
    }
    assert items[f"{instrument}#v001"]["time_utc"] == "2026-10-08T12:00:00"
    assert items["ancillary#v001"]["ancillary_files"] == (ancillary_files or {})
    assert "spice#v001" in items
    # Only MAG writes housekeeping items.
    assert ("mag_hk#v001" in items) == (instrument == "mag")


def test_process_day_unknown_instrument(mock_process_day_inputs):
    """Test that an unknown instrument raises."""
    with pytest.raises(ValueError, match="Unknown instrument"):
        process_day("bad", datetime(2026, 10, 8), "v001", "https://url", MagicMock())


@patch(f"{MODULE}.process_day", side_effect=IalirtDataGapError("gap"))
@patch(f"{MODULE}.boto3")
def test_lambda_handler_error(mock_boto3, mock_process_day, monkeypatch, caplog):
    """Test that a failure is logged and re-raised."""
    monkeypatch.setenv("INSTRUMENT", "mag")
    monkeypatch.setenv("SSR_TABLE", "ialirt-ssr-table")
    monkeypatch.setenv("SSM_API_KEY_PARAMETER", "/imap-sdc/batch-jobs/api-key")
    # Restore the global imap_data_access config after the test.
    monkeypatch.setitem(imap_data_access.config, "API_KEY", None)

    with pytest.raises(IalirtDataGapError):
        lambda_handler({"day": "20261008", "version": "v001"}, {})

    assert "FAILED mag 20261008 v001: IalirtDataGapError: gap" in caplog.text


@patch(f"{MODULE}.spacecraft_items")
def test_process_day_spacecraft(mock_spacecraft_items, mock_process_day_inputs):
    """Test that the spacecraft writes position items and no packets are read."""
    mock_spacecraft_items.return_value = [
        {"instrument_version": "spacecraft#v001", "time_utc": "2026-10-08T00:00:00"}
    ]
    table = MagicMock()

    process_day("spacecraft", datetime(2026, 10, 8), "v001", "https://url", table)

    batch = table.batch_writer.return_value.__enter__.return_value
    items = {
        call.kwargs["Item"]["instrument_version"]: call.kwargs["Item"]
        for call in batch.put_item.call_args_list
    }
    assert set(items) == {"spice#v001", "spacecraft#v001", "ancillary#v001"}
    assert items["ancillary#v001"]["ancillary_files"] == {}
    mock_process_day_inputs["parse_packets"].assert_not_called()
