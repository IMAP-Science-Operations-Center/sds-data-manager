"""Tests for narrowing the reprocessing range triggered by growing SPICE kernels.

Attitude history and pointing attitude kernels grow over time by appending new
segments to the same file series, occasionally get a version bump for corrected
data, and occasionally roll over to a new start date. These tests cover:

  - `spice.subtract_intervals`, the pure interval-diffing helper.
  - `spice.get_growing_kernel_trigger_ranges`, which applies the three
    detection rules (version bump, extends coverage, new start date).
  - The wiring of that logic into
    `IMAPJobHandler.trigger_from_new_non_science_inputs`, verifying that a
    growing kernel only triggers reprocessing for genuinely new coverage.
"""

import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dagster import DagsterInstance, build_sensor_context

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import spice
from sds_data_manager.orchestration.imap_dagster import dependency_config
from sds_data_manager.orchestration.imap_job import IMAPJobHandler

UTC = datetime.timezone.utc


def _dt(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)


def _iso_intervals(pairs):
    """Build [start, end] ISO string pairs like those stored in the DB."""
    return [[start.isoformat(), end.isoformat()] for start, end in pairs]


def _file(file_name, min_dt, max_dt, intervals=None):
    """Build a lightweight stand-in for a SPICEFiles row."""
    return SimpleNamespace(
        file_name=file_name,
        min_date_datetime=min_dt,
        max_date_datetime=max_dt,
        file_intervals_datetime=_iso_intervals(intervals)
        if intervals is not None
        else _iso_intervals([[min_dt, max_dt]]),
    )


# ---------------------------------------------------------------------------
# subtract_intervals
# ---------------------------------------------------------------------------


class TestSubtractIntervals:
    """Tests for the pure interval-subtraction helper."""

    def test_fully_covered_returns_nothing(self):
        """No leftover when the new interval exactly matches the old one."""
        new = [[_dt("2025-01-01T00:00:00"), _dt("2025-01-02T00:00:00")]]
        old = [[_dt("2025-01-01T00:00:00"), _dt("2025-01-02T00:00:00")]]
        assert spice.subtract_intervals(new, old) == []

    def test_no_overlap_returns_entire_new_interval(self):
        """The entire new interval is leftover when nothing overlaps it."""
        new = [[_dt("2025-01-02T00:00:00"), _dt("2025-01-03T00:00:00")]]
        old = [[_dt("2025-01-01T00:00:00"), _dt("2025-01-01T12:00:00")]]
        assert spice.subtract_intervals(new, old) == new

    def test_new_trailing_segment_appended(self):
        """A brand new segment appended after the old coverage ends."""
        new = [
            [_dt("2025-01-01T00:00:00"), _dt("2025-01-02T00:00:00")],
            [_dt("2025-01-02T00:00:00"), _dt("2025-01-03T00:00:00")],
        ]
        old = [[_dt("2025-01-01T00:00:00"), _dt("2025-01-02T00:00:00")]]
        result = spice.subtract_intervals(new, old)
        assert result == [[_dt("2025-01-02T00:00:00"), _dt("2025-01-03T00:00:00")]]

    def test_partial_extension_of_last_segment(self):
        """The last old segment's end grew slightly further in the new file."""
        new = [[_dt("2025-01-01T00:00:00"), _dt("2025-01-02T12:00:00")]]
        old = [[_dt("2025-01-01T00:00:00"), _dt("2025-01-02T00:00:00")]]
        result = spice.subtract_intervals(new, old)
        assert result == [[_dt("2025-01-02T00:00:00"), _dt("2025-01-02T12:00:00")]]

    def test_partial_extension_plus_new_segment_with_gap(self):
        """Combines a partial extension of the last segment with a brand new one."""
        new = [
            [_dt("2025-01-01T00:00:00"), _dt("2025-01-02T12:00:00")],
            [_dt("2025-01-03T00:00:00"), _dt("2025-01-04T00:00:00")],
        ]
        old = [[_dt("2025-01-01T00:00:00"), _dt("2025-01-02T00:00:00")]]
        result = spice.subtract_intervals(new, old)
        assert result == [
            [_dt("2025-01-02T00:00:00"), _dt("2025-01-02T12:00:00")],
            [_dt("2025-01-03T00:00:00"), _dt("2025-01-04T00:00:00")],
        ]

    def test_multiple_old_intervals_with_gap_between(self):
        """Old coverage has a gap; only the gap and the tail are new."""
        new = [[_dt("2025-01-01T00:00:00"), _dt("2025-01-05T00:00:00")]]
        old = [
            [_dt("2025-01-01T00:00:00"), _dt("2025-01-02T00:00:00")],
            [_dt("2025-01-03T00:00:00"), _dt("2025-01-04T00:00:00")],
        ]
        result = spice.subtract_intervals(new, old)
        assert result == [
            [_dt("2025-01-02T00:00:00"), _dt("2025-01-03T00:00:00")],
            [_dt("2025-01-04T00:00:00"), _dt("2025-01-05T00:00:00")],
        ]

    def test_empty_old_intervals_returns_all_new(self):
        """No predecessor coverage at all means everything is leftover."""
        new = [[_dt("2025-01-01T00:00:00"), _dt("2025-01-02T00:00:00")]]
        assert spice.subtract_intervals(new, []) == new


# ---------------------------------------------------------------------------
# get_growing_kernel_trigger_ranges
# ---------------------------------------------------------------------------


class TestGetGrowingKernelTriggerRanges:
    """Tests for the three-case detection logic, using a mocked DB session."""

    def _mock_session(self, predecessor):
        """Return a mock session whose predecessor lookup returns `predecessor`."""
        session = MagicMock()
        first_mock = (
            session.query.return_value.filter.return_value.order_by.return_value.first
        )
        first_mock.return_value = predecessor
        return session

    def test_no_predecessor_triggers_full_range(self):
        """Case 3: a new start date - nothing shares it, so trigger everything."""
        new_file = _file(
            "imap_2025_100_2025_150_01.ah.bc",
            _dt("2025-04-10T00:00:00"),
            _dt("2025-05-30T00:00:00"),
        )
        session = self._mock_session(None)
        ranges = spice.get_growing_kernel_trigger_ranges(
            session, "attitude_history", [new_file]
        )
        assert ranges == [(new_file.min_date_datetime, new_file.max_date_datetime)]

    def test_version_bump_same_coverage_triggers_full_range(self):
        """Case 1: identical coverage, higher version -> full range."""
        min_dt = _dt("2025-01-01T00:00:00")
        max_dt = _dt("2025-03-01T00:00:00")
        predecessor = _file("imap_2025_001_2025_060_01.ah.bc", min_dt, max_dt)
        new_file = _file("imap_2025_001_2025_060_02.ah.bc", min_dt, max_dt)

        session = self._mock_session(predecessor)
        ranges = spice.get_growing_kernel_trigger_ranges(
            session, "attitude_history", [new_file]
        )
        assert ranges == [(min_dt, max_dt)]

    def test_extends_coverage_triggers_only_new_segment(self):
        """Case 2: coverage extended - only the new segment is triggered."""
        min_dt = _dt("2025-01-01T00:00:00")
        old_max = _dt("2025-01-05T00:00:00")
        new_max = _dt("2025-01-10T00:00:00")

        predecessor = _file(
            "imap_2025_001_2025_005_01.ah.bc",
            min_dt,
            old_max,
            intervals=[[min_dt, old_max]],
        )
        new_file = _file(
            "imap_2025_001_2025_010_01.ah.bc",
            min_dt,
            new_max,
            intervals=[[min_dt, old_max], [old_max, new_max]],
        )

        session = self._mock_session(predecessor)
        ranges = spice.get_growing_kernel_trigger_ranges(
            session, "attitude_history", [new_file]
        )
        assert ranges == [(old_max, new_max)]

    def test_extends_coverage_with_no_interval_data_falls_back_to_full_range(self):
        """Defensive fallback: missing segment data still triggers reprocessing."""
        min_dt = _dt("2025-01-01T00:00:00")
        old_max = _dt("2025-01-05T00:00:00")
        new_max = _dt("2025-01-10T00:00:00")

        predecessor = _file("imap_2025_001_2025_005_01.ah.bc", min_dt, old_max)
        new_file = _file("imap_2025_001_2025_010_01.ah.bc", min_dt, new_max)
        new_file.file_intervals_datetime = []

        session = self._mock_session(predecessor)
        ranges = spice.get_growing_kernel_trigger_ranges(
            session, "attitude_history", [new_file]
        )
        assert ranges == [(min_dt, new_max)]

    def test_pointing_attitude_kernel_type_uses_same_rules(self):
        """The generalized logic also applies to pointing_attitude kernels."""
        min_dt = _dt("2025-01-01T00:00:00")
        old_max = _dt("2025-01-05T00:00:00")
        new_max = _dt("2025-01-10T00:00:00")

        predecessor = _file(
            "imap_dps_2025_001_2025_005_01.ah.bc",
            min_dt,
            old_max,
            intervals=[[min_dt, old_max]],
        )
        new_file = _file(
            "imap_dps_2025_001_2025_010_01.ah.bc",
            min_dt,
            new_max,
            intervals=[[min_dt, old_max], [old_max, new_max]],
        )

        session = self._mock_session(predecessor)
        ranges = spice.get_growing_kernel_trigger_ranges(
            session, "pointing_attitude", [new_file]
        )
        assert ranges == [(old_max, new_max)]


# ---------------------------------------------------------------------------
# Wiring into IMAPJobHandler.trigger_from_new_non_science_inputs
# ---------------------------------------------------------------------------


def _mag_job_handler():
    """Return a job handler for a real job with default-triggering AH/DPS inputs.

    Uses the (mag, l1d, norm-srf) job, which lists both attitude_history and
    pointing_attitude as spice inputs without trigger_job=false overrides.
    """
    return IMAPJobHandler(dependency_config._config[("mag", "l1d", "norm-srf")])


def _session_returning(new_files, predecessor):
    """Return a mock session returning `new_files` and predecessor `predecessor`."""
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = new_files
    first_mock = (
        session.query.return_value.filter.return_value.order_by.return_value.first
    )
    first_mock.return_value = predecessor
    return session


class TestTriggerFromNewNonScienceInputsWiring:
    """Verifies the generic sensor logic narrows growing-kernel trigger ranges."""

    def test_attitude_history_only_triggers_new_daily_partitions(self):
        """Only the newly appended segment's daily partitions are triggered."""
        instance = DagsterInstance.ephemeral()
        instance.add_dynamic_partitions(
            "daily_partitions",
            [
                "daily_2025-01-01T00:00:00_to_2025-01-02T00:00:00",
                "daily_2025-01-02T00:00:00_to_2025-01-03T00:00:00",
                "daily_2025-01-03T00:00:00_to_2025-01-04T00:00:00",
            ],
        )
        context = build_sensor_context(instance=instance)

        job_handler = _mag_job_handler()
        dependency = next(
            d
            for d in job_handler.job_config.spice_inputs
            if d.source == "attitude_history"
        )

        min_dt = _dt("2025-01-01T00:00:00")
        old_max = _dt("2025-01-02T00:00:00")
        new_max = _dt("2025-01-04T00:00:00")

        predecessor = _file(
            "imap_2025_001_2025_002_01.ah.bc",
            min_dt,
            old_max,
            intervals=[[min_dt, old_max]],
        )
        new_file = _file(
            "imap_2025_001_2025_004_01.ah.bc",
            min_dt,
            new_max,
            intervals=[[min_dt, old_max], [old_max, new_max]],
        )
        new_file.ingestion_date = datetime.datetime.now(UTC)

        session = _session_returning([new_file], predecessor)
        cursors = {dependency.to_dagster_name(): "2024-01-01T00:00:00"}

        with patch.object(db, "Session") as mock_cls:
            mock_cls.return_value.__enter__.return_value = session
            mock_cls.return_value.__exit__.return_value = False
            partitions = job_handler.trigger_from_new_non_science_inputs(
                context,
                dependency,
                cursors,
                models.SPICEFiles,
                models.SPICEFiles.kernel_type,
                None,
                "min_date_datetime",
                "max_date_datetime",
            )

        # Only the two days after the predecessor's coverage should be
        # affected - the already-processed Jan 1 -> Jan 2 partition must not
        # be re-triggered.
        assert set(partitions) == {
            "daily_2025-01-02T00:00:00_to_2025-01-03T00:00:00",
            "daily_2025-01-03T00:00:00_to_2025-01-04T00:00:00",
        }

    def test_pointing_attitude_also_narrows_trigger_range(self):
        """Confirms pointing_attitude dependencies get the same narrowing."""
        instance = DagsterInstance.ephemeral()
        instance.add_dynamic_partitions(
            "daily_partitions",
            [
                "daily_2025-01-01T00:00:00_to_2025-01-02T00:00:00",
                "daily_2025-01-02T00:00:00_to_2025-01-03T00:00:00",
            ],
        )
        context = build_sensor_context(instance=instance)

        job_handler = _mag_job_handler()
        dependency = next(
            d
            for d in job_handler.job_config.spice_inputs
            if d.source == "pointing_attitude"
        )

        min_dt = _dt("2025-01-01T00:00:00")
        old_max = _dt("2025-01-01T12:00:00")
        new_max = _dt("2025-01-03T00:00:00")

        predecessor = _file(
            "imap_dps_2025_001_2025_001_01.ah.bc",
            min_dt,
            old_max,
            intervals=[[min_dt, old_max]],
        )
        new_file = _file(
            "imap_dps_2025_001_2025_003_01.ah.bc",
            min_dt,
            new_max,
            intervals=[[min_dt, old_max], [old_max, new_max]],
        )
        new_file.ingestion_date = datetime.datetime.now(UTC)

        session = _session_returning([new_file], predecessor)
        cursors = {dependency.to_dagster_name(): "2024-01-01T00:00:00"}

        with patch.object(db, "Session") as mock_cls:
            mock_cls.return_value.__enter__.return_value = session
            mock_cls.return_value.__exit__.return_value = False
            partitions = job_handler.trigger_from_new_non_science_inputs(
                context,
                dependency,
                cursors,
                models.SPICEFiles,
                models.SPICEFiles.kernel_type,
                None,
                "min_date_datetime",
                "max_date_datetime",
            )

        assert set(partitions) == {
            "daily_2025-01-01T00:00:00_to_2025-01-02T00:00:00",
            "daily_2025-01-02T00:00:00_to_2025-01-03T00:00:00",
        }
