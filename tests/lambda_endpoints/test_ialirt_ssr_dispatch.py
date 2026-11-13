"""Test the I-ALiRT SSR dispatch lambda."""

# ruff: noqa: E402
import sys
from unittest.mock import patch

import pytest

from sds_data_manager.lambda_code.IAlirtCode import ialirt_ssr_utils

# The Lambda imports the SSR modules by their plain names.
sys.modules["ialirt_ssr_utils"] = ialirt_ssr_utils
from sds_data_manager.lambda_code.IAlirtCode import ialirt_ssr_processing

sys.modules["ialirt_ssr_processing"] = ialirt_ssr_processing

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_dispatch import (
    SSR_INSTRUMENTS,
    IalirtDataGapError,
    get_days,
    is_complete,
    is_processed,
    lambda_handler,
)

MODULE = "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_dispatch"


def only_20260907_complete(day):
    """Report only 20260907 as complete."""
    return day == "20260907"


@patch.dict(f"{MODULE}.imap_data_access.config")
@patch(f"{MODULE}.is_complete", side_effect=only_20260907_complete)
@patch(f"{MODULE}.imap_data_access.query")
@patch(f"{MODULE}.boto3.client")
def test_get_days_daily(
    mock_client, mock_query, mock_complete, setup_ssr_table, monkeypatch, tmp_path
):
    """Test that the daily trigger returns only missing instruments on complete days."""
    monkeypatch.setenv("SSM_API_KEY_PARAMETER", "param")
    monkeypatch.setattr(f"{MODULE}.DOWNLOAD_DIR", tmp_path / "ssr")
    # Two versions of one file, so 20260906 to 20260908 are candidates.
    mock_query.return_value = [{"start_date": "20260907"}, {"start_date": "20260907"}]
    table = setup_ssr_table["ssr_table"]
    # mag already has data for 20260907. 20260908 is fully processed.
    table.put_item(
        Item={"instrument_version": "mag#v001", "time_utc": "2026-09-07T12:00:00"}
    )
    for instrument in SSR_INSTRUMENTS:
        table.put_item(
            Item={
                "instrument_version": f"{instrument}#v001",
                "time_utc": "2026-09-08T12:00:00",
            }
        )

    assert get_days({}, SSR_INSTRUMENTS) == {"20260907": SSR_INSTRUMENTS[1:]}
    # 20260908 is skipped before the coverage check.
    assert [call.args[0] for call in mock_complete.call_args_list] == [
        "20260906",
        "20260907",
    ]


def test_get_days_manual():
    """Test that a manual range includes the start and end dates."""
    detail = {"start_date": "20260930", "end_date": "20261002"}

    assert get_days(detail, ["mag"]) == {
        "20260930": ["mag"],
        "20261001": ["mag"],
        "20261002": ["mag"],
    }


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_dispatch.boto3.client")
def test_lambda_handler(mock_client):
    """Test that each instrument is invoked for each day."""
    event = {
        "detail": {
            "start_date": "20260930",
            "end_date": "20261001",
            "instruments": ["mag"],
        }
    }

    lambda_handler(event, {})

    mock_invoke = mock_client.return_value.invoke
    assert mock_invoke.call_count == 2
    mock_invoke.assert_called_with(
        FunctionName="ialirt-ssr-mag",
        InvocationType="Event",
        Payload='{"day": "20261001", "version": "v001"}',
    )


def test_lambda_handler_bad_version():
    """Test that an invalid version raises."""
    with pytest.raises(ValueError, match="version"):
        lambda_handler({"detail": {"version": "2"}}, {})


def test_get_days_end_before_start():
    """Test that an end date before the start date raises."""
    detail = {"start_date": "20261002", "end_date": "20260930"}

    with pytest.raises(ValueError, match="before start_date"):
        get_days(detail, ["mag"])


def test_lambda_handler_unknown_instrument():
    """Test that an unknown instrument raises."""
    with pytest.raises(ValueError, match="Unknown instruments"):
        lambda_handler({"detail": {"instruments": ["mag", "bad"]}}, {})


def test_is_processed(setup_ssr_table):
    """Test that only days with items for the instrument count as processed."""
    table = setup_ssr_table["ssr_table"]
    table.put_item(
        Item={"instrument_version": "mag#v001", "time_utc": "2026-09-07T12:00:00"}
    )

    assert is_processed(table, "mag", "20260907", "v001")
    assert not is_processed(table, "mag", "20260908", "v001")
    assert not is_processed(table, "hit", "20260907", "v001")
    assert not is_processed(table, "mag", "20260907", "v002")


@patch(f"{MODULE}.check_day_coverage")
@patch(f"{MODULE}.parse_packets")
@patch(f"{MODULE}.query_filenames")
@patch(f"{MODULE}.spiceypy.KernelPool")
@patch(f"{MODULE}.download_spice_files", return_value=[])
@patch(f"{MODULE}.get_latest_spice_kernels")
def test_is_complete(
    mock_kernels,
    mock_download,
    mock_kernel_pool,
    mock_query,
    mock_parse,
    mock_check,
    monkeypatch,
):
    """Test that a day is complete only when its files exist with no gaps."""
    monkeypatch.setenv("IMAP_DATA_ACCESS_URL", "url")
    assert is_complete("20260907")

    mock_check.side_effect = IalirtDataGapError("gap")
    assert not is_complete("20260907")

    # A file with no I-ALiRT packets.
    mock_check.side_effect = None
    mock_parse.side_effect = KeyError(478)
    assert not is_complete("20260907")

    # The kernels cannot be downloaded.
    mock_parse.side_effect = None
    mock_download.side_effect = OSError("download failed")
    assert not is_complete("20260907")

    # Kernels are not requested when a file is missing.
    mock_kernels.reset_mock()
    mock_query.side_effect = FileNotFoundError("missing")
    assert not is_complete("20260907")
    mock_kernels.assert_not_called()
