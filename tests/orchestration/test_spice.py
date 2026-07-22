"""Tests for time-conversion behavior in sds_data_manager/orchestration/spice.py."""

import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import spiceypy

from sds_data_manager.orchestration import spice

TEST_LSK_PATH = (
    Path(__file__).parent.parent / "test-data" / "test_spice_files" / "naif0012.tls"
)


def _query_times(dependencies, start_date, end_date):
    """Call get_upstream_dependency_inputs_spice and capture the query times sent."""
    captured = {}

    def fake_lambda_handler(event, context):
        captured.update(event["queryStringParameters"])
        return {"statusCode": 200, "body": '["imap_2025_118_2025_120_001.ah.bc"]'}

    with patch.object(
        spice.spice_metakernel_api, "lambda_handler", side_effect=fake_lambda_handler
    ):
        spice.get_upstream_dependency_inputs_spice(dependencies, start_date, end_date)

    return captured["start_time"], captured["end_time"]


def test_seconds_since_j2000_matches_authoritative_spiceypy_conversion():
    """Our hardcoded-epoch conversion must track spiceypy's own conversion closely.

    `_seconds_since_j2000` intentionally avoids spiceypy.datetime2et() (which
    needs a leapseconds kernel furnished) so this function stays free of any
    S3/database dependency at runtime - see the comment above
    `_TTJ2000_EPOCH_UTC` in spice.py. This test furnishes a real leapseconds
    kernel *only* to independently verify the hardcoded epoch is correct, not
    because production code needs it.
    """
    # Furnished directly (not via spiceypy.KernelPool), since KernelPool
    # snapshots and later re-furnishes every kernel already in spiceypy's
    # global kernel pool on exit - if another test in the same process had
    # already furnished a kernel from a transient path (e.g. a leapseconds
    # kernel downloaded to a shared /tmp path), that restore can fail if the
    # path no longer exists. Furnishing this real, permanent repo file
    # directly and leaving it loaded matches how every other test in this
    # suite handles SPICE kernels.
    # TODO: make a spice furnishig fixture - see imap_processing
    spiceypy.furnsh(str(TEST_LSK_PATH))
    dt = datetime.datetime(2025, 6, 1, 14, 32, 10, tzinfo=datetime.timezone.utc)

    # Tolerance covers the leap seconds added since the J2000 epoch (2000)
    # that this simplified conversion doesn't account for (5, as of
    # 2017-01-01) - negligible for identifying which SPICE kernels cover a
    # given time range.
    assert spice._seconds_since_j2000(dt) == pytest.approx(
        spiceypy.datetime2et(dt), abs=6
    )


def test_preserves_sub_day_precision_within_same_calendar_day():
    """A sub-day window on one calendar day must not collapse to zero width."""
    start = datetime.datetime(2025, 6, 1, 14, 32, 10, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2025, 6, 1, 21, 47, 33, tzinfo=datetime.timezone.utc)

    start_time, end_time = _query_times(["attitude_history"], start, end)

    assert start_time != end_time
    expected_duration = (end - start).total_seconds()
    assert (end_time - start_time) == pytest.approx(expected_duration, abs=1e-9)


def test_preserves_sub_day_precision_across_midnight():
    """A window crossing midnight must use the true end time, not day-rounded."""
    start = datetime.datetime(2025, 6, 1, 22, 0, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2025, 6, 2, 3, 0, 0, tzinfo=datetime.timezone.utc)

    start_time, end_time = _query_times(["attitude_history"], start, end)

    expected_duration = (end - start).total_seconds()
    assert (end_time - start_time) == pytest.approx(expected_duration, abs=1e-9)


def test_j2000_conversion_uses_true_tt_epoch_instant():
    """The query time must use the true J2000 (TT) epoch, not a nominal UTC noon.

    Regression test for approximating the J2000 epoch as a nominal
    2000-01-01T12:00:00 UTC instant: the J2000 epoch is actually defined in
    TT, which was already offset from UTC by 64.184s at that date.
    """
    start = datetime.datetime(2025, 6, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2025, 6, 1, 1, 0, 0, tzinfo=datetime.timezone.utc)

    start_time, _ = _query_times(["attitude_history"], start, end)

    naive_j2000 = datetime.datetime(2000, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    naive_start_time = (start - naive_j2000).total_seconds()

    assert start_time - naive_start_time == pytest.approx(64.184)


def test_same_start_and_end_extends_query_window_by_24_hours():
    """Equal start/end dates should query a full 24-hour window, at full precision."""
    instant = datetime.datetime(2025, 6, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)

    start_time, end_time = _query_times(["attitude_history"], instant, instant)

    assert (end_time - start_time) == pytest.approx(24 * 3600, abs=1e-9)
