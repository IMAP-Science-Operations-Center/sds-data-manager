"""Tests for the L0 data gap filler asset and weekly sensor."""

import datetime
from unittest import mock

from dagster import RunRequest, SkipReason, build_sensor_context

from sds_data_manager.orchestration import data_gap_filler
from sds_data_manager.orchestration.data_gap_filler import (
    fill_data_gaps,
    get_gap_check_window,
    l0_data_gap_fill_sensor,
)

UTC = datetime.timezone.utc


def test_gap_check_window_is_month_ending_one_week_ago():
    """The window is (a week ago - a month, a week ago), at midnight UTC."""
    # Monday 2026-10-12
    now = datetime.datetime(2026, 10, 12, 6, 30, tzinfo=UTC)
    start, end = get_gap_check_window(now)
    assert end == datetime.datetime(2026, 10, 5, tzinfo=UTC)
    assert start == datetime.datetime(2026, 9, 5, tzinfo=UTC)


def test_gap_check_window_clamps_day_for_short_months():
    """Subtracting a month from Mar 31 lands on Feb 28, not an error."""
    now = datetime.datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    start, end = get_gap_check_window(now)
    assert end == datetime.datetime(2026, 3, 31, tzinfo=UTC)
    assert start == datetime.datetime(2026, 2, 28, tzinfo=UTC)


def _mock_webpoda(tmp_path, repoint_file=True):
    """Return a mock webpoda module with two instruments of each kind."""
    mock_webpoda = mock.MagicMock()
    mock_webpoda.INSTRUMENT_APIDS = {
        "mag": [100],  # in-situ
        "swe": [200],  # in-situ
        "glows": [300],  # repointing (ENA)
        "hi": [400],  # repointing (ENA)
    }
    if repoint_file:
        repoint_path = tmp_path / "repoint.csv"
        repoint_path.write_text(
            "repoint_start_utc,repoint_end_utc,repoint_id\n"
            "2026-09-05T00:00:00,2026-09-06T00:00:00,1\n"
        )
        mock_webpoda.get_repoint_file.return_value = repoint_path
    else:
        mock_webpoda.get_repoint_file.return_value = None
    return mock_webpoda


def test_fill_data_gaps_queries_all_instruments_in_sct_mode(tmp_path):
    """Every instrument is queried by S/C time, uploading only new data."""
    start = datetime.datetime(2026, 9, 5, tzinfo=UTC)
    end = datetime.datetime(2026, 10, 5, tzinfo=UTC)
    mock_webpoda = _mock_webpoda(tmp_path)

    with mock.patch.object(data_gap_filler, "webpoda", mock_webpoda):
        summary = fill_data_gaps(start, end)

    # In-situ instruments go through the daily download, in SCT mode.
    daily_instruments = {
        call.kwargs["instrument"]
        for call in mock_webpoda.download_daily_data.call_args_list
    }
    assert daily_instruments == {"mag", "swe"}
    for call in mock_webpoda.download_daily_data.call_args_list:
        assert call.kwargs["start_time"] == start
        assert call.kwargs["end_time"] == end
        assert call.kwargs["query_by_ert"] is False
        assert call.kwargs["upload_to_sdc"] is True

    # ENA instruments go through the repointing download, with the repoint
    # table parsed into rows, also in SCT mode.
    repoint_instruments = {
        call.kwargs["instrument"]
        for call in mock_webpoda.download_repointing_data.call_args_list
    }
    assert repoint_instruments == {"glows", "hi"}
    for call in mock_webpoda.download_repointing_data.call_args_list:
        assert call.kwargs["query_by_ert"] is False
        assert call.kwargs["upload_to_sdc"] is True
        assert call.kwargs["repoint_data"] == [
            {
                "repoint_start_utc": "2026-09-05T00:00:00",
                "repoint_end_utc": "2026-09-06T00:00:00",
                "repoint_id": "1",
            }
        ]

    assert summary["daily_instruments"] == ["mag", "swe"]
    assert summary["repointing_instruments"] == ["glows", "hi"]


def test_fill_data_gaps_skips_ena_without_repoint_file(tmp_path):
    """With no repointing file, ENA instruments are skipped, not fatal."""
    start = datetime.datetime(2026, 9, 5, tzinfo=UTC)
    end = datetime.datetime(2026, 10, 5, tzinfo=UTC)
    mock_webpoda = _mock_webpoda(tmp_path, repoint_file=False)

    with mock.patch.object(data_gap_filler, "webpoda", mock_webpoda):
        summary = fill_data_gaps(start, end)

    mock_webpoda.download_daily_data.assert_called()
    mock_webpoda.download_repointing_data.assert_not_called()
    assert summary["repointing_instruments"] == []


def _evaluate_sensor(now):
    """Evaluate the gap fill sensor as if the time were ``now``.

    The sensor is a generator, so it must be consumed while ``_now`` is
    still patched.
    """
    with mock.patch.object(data_gap_filler, "_now", return_value=now):
        return _run_requests(l0_data_gap_fill_sensor(build_sensor_context()))


def _run_requests(result):
    """Normalize a sensor evaluation result to its list of run requests."""
    if isinstance(result, SkipReason):
        return []
    if hasattr(result, "run_requests"):
        return list(result.run_requests)
    return list(result)


def test_sensor_yields_one_run_request_per_evaluation():
    """Every evaluation yields exactly one run request.

    The weekly cadence comes from the sensor's minimum interval, not from
    the time of evaluation, so any day/time behaves the same.
    """
    # Tuesday 2026-10-13
    run_requests = _evaluate_sensor(datetime.datetime(2026, 10, 13, 6, 15, tzinfo=UTC))
    assert len(run_requests) == 1
    assert isinstance(run_requests[0], RunRequest)
    # The window for this run ends one week earlier, on 2026-10-06.
    assert run_requests[0].run_key == "l0_data_gap_fill_20261006"


def test_sensor_run_key_changes_with_the_week():
    """Different weeks produce different run keys, so runs never collide."""
    first = _evaluate_sensor(datetime.datetime(2026, 10, 13, tzinfo=UTC))
    second = _evaluate_sensor(datetime.datetime(2026, 10, 20, tzinfo=UTC))
    assert first[0].run_key != second[0].run_key


def test_sensor_minimum_interval_is_one_week():
    """The sensor is evaluated at most once per config interval (a week)."""
    from sds_data_manager.orchestration import config

    sensor_def = l0_data_gap_fill_sensor
    assert (
        sensor_def.minimum_interval_seconds
        == config.DATA_GAP_FILLER_INTERVAL.total_seconds()
    )


def test_defs_include_gap_filler():
    """The gap filler asset, job, and sensor are registered in Dagster."""
    from sds_data_manager.orchestration.imap_dagster import defs

    asset_graph = defs.get_repository_def().asset_graph
    asset_keys = {key.to_user_string() for key in asset_graph.get_all_asset_keys()}
    assert "l0_data_gap_fill" in asset_keys

    assert defs.get_job_def("l0_data_gap_fill_job") is not None
    assert defs.get_sensor_def("l0_data_gap_fill_sensor") is not None
