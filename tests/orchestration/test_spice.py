"""Tests for time-conversion behavior in sds_data_manager/orchestration/spice.py."""

import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import spiceypy

from sds_data_manager.orchestration import spice
from sds_data_manager.orchestration.spin import get_upstream_dependency_inputs_spin
from tests.orchestration.conftest import _insert_spin_file

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


def test_get_upstream_dependency_inputs_spin(mock_db_session):
    """Test get_upstream_dependency_inputs_spin returns files in the correct order."""
    # Add some test spin file records
    # The first two files are for the same date range but have different versions.
    # The v02 should be picked over the v01.
    _insert_spin_file(
        mock_db_session,
        "imap_2026_142_2026_143_01.spin",
        upload_time=1,
        start_date=datetime.datetime(2026, 1, 1),
        end_date=datetime.datetime(2026, 1, 2),
    )
    _insert_spin_file(
        mock_db_session,
        "imap_2026_142_2026_143_02.spin",
        upload_time=2,
        start_date=datetime.datetime(2026, 1, 1),
        end_date=datetime.datetime(2026, 1, 2),
    )
    # The third and fourth files have the same version and overlapping date
    # ranges. The start date should take precedence over the ingest order when
    # sorting them, so the later-starting file is listed last even though it was
    # ingested first (When loading SPICE kernels last one takes precedence).
    _insert_spin_file(
        mock_db_session,
        "imap_2026_126_2026_128_01.spin",
        upload_time=3,
        start_date=datetime.datetime(2026, 1, 10),
        end_date=datetime.datetime(2026, 1, 12),
    )
    _insert_spin_file(
        mock_db_session,
        "imap_2026_120_2026_127_01.spin",
        upload_time=4,
        start_date=datetime.datetime(2026, 1, 2),
        end_date=datetime.datetime(2026, 1, 11),
    )
    # Query for a wide range of spin files.
    spin_files = get_upstream_dependency_inputs_spin(
        datetime.datetime(2025, 6, 1),
        datetime.datetime(2027, 6, 10),
        False,
        mock_db_session,
    )
    # Check that the returned spin files are in reverse priority order (highest
    # version last, then latest start date last) and that the correct versions
    # were selected.
    assert spin_files == [
        "imap_2026_120_2026_127_01.spin",
        "imap_2026_126_2026_128_01.spin",
        "imap_2026_142_2026_143_02.spin",
    ]


def test_get_upstream_dependency_inputs_spin_superseded_by_new_ranges(
    mock_db_session,
):
    """Test older spin files are dropped when newer versions split days differently.

    Regression test for repoint89 (2025-12-25T10:02:13 to 2025-12-26T10:02:10),
    which was given imap_2025_360_2025_360_01.spin alongside the _10 files that
    replace it.
    """

    def doy(day):
        return datetime.datetime(2025, 1, 1) + datetime.timedelta(days=day - 1)

    old_files = [
        ("imap_2025_358_2025_359_01.spin", 358, 359),
        ("imap_2025_359_2025_360_01.spin", 359, 360),
        ("imap_2025_360_2025_360_01.spin", 360, 360),
        # Only partly covered by the _10 files, so it is still needed.
        ("imap_2025_361_2025_362_01.spin", 361, 362),
    ]
    new_files = [
        ("imap_2025_358_2025_359_10.spin", 358, 359),
        ("imap_2025_359_2025_360_10.spin", 359, 360),
        ("imap_2025_360_2025_361_10.spin", 360, 361),
    ]
    for upload_time, (filename, start, end) in enumerate(old_files + new_files):
        _insert_spin_file(
            mock_db_session,
            filename,
            upload_time=upload_time,
            start_date=doy(start),
            end_date=doy(end),
        )

    # The job floors the partition start to midnight before querying.
    spin_files = get_upstream_dependency_inputs_spin(
        datetime.datetime(2025, 12, 25),
        datetime.datetime(2025, 12, 26, 10, 2, 10),
        False,
        mock_db_session,
    )
    assert spin_files == [
        "imap_2025_358_2025_359_10.spin",
        "imap_2025_359_2025_360_10.spin",
        "imap_2025_360_2025_361_10.spin",
    ]

    # Day 362 has no _10 coverage, so the _01 file spanning 361-362 is kept.
    spin_files = get_upstream_dependency_inputs_spin(
        doy(361),
        doy(362),
        False,
        mock_db_session,
    )
    assert spin_files == [
        "imap_2025_361_2025_362_01.spin",
        "imap_2025_360_2025_361_10.spin",
    ]

    # A lower version ingested after the _10 files must not take priority.
    _insert_spin_file(
        mock_db_session,
        "imap_2025_360_2025_360_02.spin",
        upload_time=10,
        start_date=doy(360),
        end_date=doy(360),
    )
    spin_files = get_upstream_dependency_inputs_spin(
        datetime.datetime(2025, 12, 25),
        datetime.datetime(2025, 12, 26, 10, 2, 10),
        False,
        mock_db_session,
    )
    assert spin_files == [
        "imap_2025_358_2025_359_10.spin",
        "imap_2025_359_2025_360_10.spin",
        "imap_2025_360_2025_361_10.spin",
    ]


@pytest.mark.parametrize(
    ("window_start", "window_end", "expected"),
    [
        # Window on the first day of the _10 span
        (358, 358, ["imap_2025_358_2025_359_10.spin"]),
        # Window on the last day of the _10 span
        (360, 360, ["imap_2025_359_2025_360_10.spin"]),
        # Window covering the whole _10 span
        (
            358,
            360,
            ["imap_2025_358_2025_359_10.spin", "imap_2025_359_2025_360_10.spin"],
        ),
    ],
)
def test_get_upstream_dependency_inputs_spin_superseded_at_span_edges(
    mock_db_session, window_start, window_end, expected
):
    """Test single-day files on the first or last day of a newer span are dropped.

    The _10 files cover days 358-360, so single-day _01 files on day 358 or
    day 360 are fully covered and must not be returned.
    """

    def doy(day):
        return datetime.datetime(2025, 1, 1) + datetime.timedelta(days=day - 1)

    files = [
        ("imap_2025_358_2025_358_01.spin", 358, 358),
        ("imap_2025_360_2025_360_01.spin", 360, 360),
        ("imap_2025_358_2025_359_10.spin", 358, 359),
        ("imap_2025_359_2025_360_10.spin", 359, 360),
    ]
    for upload_time, (filename, start, end) in enumerate(files):
        _insert_spin_file(
            mock_db_session,
            filename,
            upload_time=upload_time,
            start_date=doy(start),
            end_date=doy(end),
        )

    spin_files = get_upstream_dependency_inputs_spin(
        doy(window_start),
        doy(window_end) + datetime.timedelta(hours=10),
        False,
        mock_db_session,
    )
    assert spin_files == expected


@pytest.mark.parametrize(
    ("window_start", "window_end", "expected"),
    [
        # Window inside the _10 span: the _01 file extending before it is dropped
        (
            359,
            359,
            ["imap_2025_358_2025_359_10.spin", "imap_2025_359_2025_360_10.spin"],
        ),
        # Window inside the _10 span: the _01 file extending after it is dropped
        (360, 360, ["imap_2025_359_2025_360_10.spin"]),
        # Window covering the whole _10 span: both _01 files are dropped
        (
            358,
            360,
            ["imap_2025_358_2025_359_10.spin", "imap_2025_359_2025_360_10.spin"],
        ),
        # Window reaching day 357, which only the _01 file covers: it is kept
        (
            357,
            358,
            ["imap_2025_357_2025_359_01.spin", "imap_2025_358_2025_359_10.spin"],
        ),
        # Window reaching day 361, which only the _01 file covers: it is kept
        (
            360,
            361,
            ["imap_2025_360_2025_362_01.spin", "imap_2025_359_2025_360_10.spin"],
        ),
    ],
)
def test_get_upstream_dependency_inputs_spin_superseded_within_window(
    mock_db_session, window_start, window_end, expected
):
    """Test supersession only considers the days inside the query window.

    The _10 files cover days 358-360. The _01 files extend past that span on
    either side, so higher versions never cover their whole filename range,
    but they must still be dropped when the window falls inside the _10 span.
    """

    def doy(day):
        return datetime.datetime(2025, 1, 1) + datetime.timedelta(days=day - 1)

    files = [
        ("imap_2025_357_2025_359_01.spin", 357, 359),
        ("imap_2025_360_2025_362_01.spin", 360, 362),
        ("imap_2025_358_2025_359_10.spin", 358, 359),
        ("imap_2025_359_2025_360_10.spin", 359, 360),
    ]
    for upload_time, (filename, start, end) in enumerate(files):
        _insert_spin_file(
            mock_db_session,
            filename,
            upload_time=upload_time,
            start_date=doy(start),
            end_date=doy(end),
        )

    spin_files = get_upstream_dependency_inputs_spin(
        doy(window_start),
        doy(window_end) + datetime.timedelta(hours=10),
        False,
        mock_db_session,
    )
    assert spin_files == expected
