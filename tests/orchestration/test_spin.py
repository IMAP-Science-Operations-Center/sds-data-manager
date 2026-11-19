"""Tests for resolving spin inputs through the SPICE table."""

import datetime
import json
from unittest.mock import patch

import pytest
from dagster import build_sensor_context

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import spice
from sds_data_manager.orchestration.custom_behavior.mag import MagL1DJob
from sds_data_manager.orchestration.imap_dagster import defs, job_handlers
from sds_data_manager.orchestration.imap_job import MissingDependenciesError
from tests.orchestration.conftest import insert_spin_file

UTC = datetime.timezone.utc


def _utc(*args):
    return datetime.datetime(*args, tzinfo=UTC)


@pytest.fixture
def job():
    """Return the MAG L1D handler, a job with a required spin input."""
    handler = next(j for j in job_handlers if isinstance(j, MagL1DJob))
    assert handler.job_config.spin_input.required
    return handler


def _spin_inputs(job, start, end):
    return job.get_spin_files_inputs(None, start, end)


# A pointing partition that does not start at midnight
POINTING_START = _utc(2026, 1, 3, 10, 2, 13)
POINTING_END = _utc(2026, 1, 4, 10, 2, 10)


def test_required_spin_covering_pointing_runs(mock_db_session, job):
    """Spin covering a pointing that starts mid-day is returned."""
    insert_spin_file(
        mock_db_session,
        "imap_2026_003_2026_004_01.spin.csv",
        _utc(2026, 1, 3, 9),
        _utc(2026, 1, 4, 11),
        ingestion_date=_utc(2026, 1, 5),
    )

    assert _spin_inputs(job, POINTING_START, POINTING_END) == [
        "imap_2026_003_2026_004_01.spin.csv"
    ]


def test_required_spin_ending_partway_skips(mock_db_session, job):
    """Spin ending partway through the pointing skips the job, naming the gap."""
    insert_spin_file(
        mock_db_session,
        "imap_2026_003_2026_004_01.spin.csv",
        _utc(2026, 1, 3, 9),
        _utc(2026, 1, 4, 8),
        ingestion_date=_utc(2026, 1, 5),
    )

    with pytest.raises(MissingDependenciesError, match="2026-01-04T08:00:00"):
        _spin_inputs(job, POINTING_START, POINTING_END)


def test_optional_spin_returns_partial_coverage(mock_db_session, job):
    """An optional spin input passes whatever spin exists."""
    insert_spin_file(
        mock_db_session,
        "imap_2026_003_2026_004_01.spin.csv",
        _utc(2026, 1, 3, 9),
        _utc(2026, 1, 4, 8),
        ingestion_date=_utc(2026, 1, 5),
    )

    with patch.object(job.job_config.spin_input, "required", False):
        assert _spin_inputs(job, POINTING_START, POINTING_END) == [
            "imap_2026_003_2026_004_01.spin.csv"
        ]


def test_required_spin_with_no_files_skips(mock_db_session, job):
    """A required spin input with no spin at all skips the job."""
    with pytest.raises(MissingDependenciesError):
        _spin_inputs(job, POINTING_START, POINTING_END)


def test_mag_l1d_waits_for_the_next_spin_delivery(mock_db_session, job):
    """MAG L1D 2026-09-10 skips until a delivery covers the rest of the day.

    imap_2026_252_2026_253_01 ends at 13:47, partway through the day (#1663).
    """
    day_start, day_end = _utc(2026, 9, 10), _utc(2026, 9, 11)
    insert_spin_file(
        mock_db_session,
        "imap_2026_252_2026_253_01.spin.csv",
        _utc(2026, 9, 9, 0, 0, 5),
        _utc(2026, 9, 10, 13, 47),
        ingestion_date=_utc(2026, 9, 10, 14),
    )
    with pytest.raises(MissingDependenciesError, match="13:47"):
        _spin_inputs(job, day_start, day_end)

    # The next delivery picks up where the last one ended, within the seam
    # tolerance, and runs past midnight
    insert_spin_file(
        mock_db_session,
        "imap_2026_253_2026_254_01.spin.csv",
        _utc(2026, 9, 10, 13, 47, 0, 10000),
        _utc(2026, 9, 11, 12),
        ingestion_date=_utc(2026, 9, 11, 13),
    )
    assert _spin_inputs(job, day_start, day_end) == [
        "imap_2026_252_2026_253_01.spin.csv",
        "imap_2026_253_2026_254_01.spin.csv",
    ]


def test_long_spin_file_beside_single_day_pieces(mock_db_session, job):
    """2026-07-24: a three-day file fills what the later single-day pieces miss."""
    insert_spin_file(
        mock_db_session,
        "imap_2026_204_2026_206_02.spin.csv",
        _utc(2026, 7, 23),
        _utc(2026, 7, 26),
        ingestion_date=_utc(2026, 7, 26, 1),
    )
    insert_spin_file(
        mock_db_session,
        "imap_2026_204_2026_204_02.spin.csv",
        _utc(2026, 7, 23),
        _utc(2026, 7, 24),
        ingestion_date=_utc(2026, 7, 26, 2),
    )
    # Ends one spin short of midnight, which only the long file covers
    insert_spin_file(
        mock_db_session,
        "imap_2026_205_2026_205_02.spin.csv",
        _utc(2026, 7, 24),
        _utc(2026, 7, 24, 23, 59, 45),
        ingestion_date=_utc(2026, 7, 26, 2),
    )

    assert _spin_inputs(job, _utc(2026, 7, 24), _utc(2026, 7, 25)) == [
        "imap_2026_204_2026_206_02.spin.csv",
        "imap_2026_205_2026_205_02.spin.csv",
    ]


def test_superseded_spin_is_dropped_for_repoint89(mock_db_session, job):
    """#1708: _10 files that split the days differently replace the _01 files.

    Repoint 89 was given imap_2025_360_2025_360_01 alongside the _10 files.
    """
    files = [
        ("imap_2025_358_2025_359_01.spin.csv", (2025, 12, 24), (2025, 12, 26)),
        ("imap_2025_359_2025_360_01.spin.csv", (2025, 12, 25), (2025, 12, 27)),
        ("imap_2025_360_2025_360_01.spin.csv", (2025, 12, 26), (2025, 12, 27)),
        ("imap_2025_361_2025_362_01.spin.csv", (2025, 12, 27), (2025, 12, 29)),
        ("imap_2025_358_2025_359_10.spin.csv", (2025, 12, 24), (2025, 12, 26)),
        ("imap_2025_359_2025_360_10.spin.csv", (2025, 12, 25), (2025, 12, 27)),
        ("imap_2025_360_2025_361_10.spin.csv", (2025, 12, 26), (2025, 12, 28)),
    ]
    for hour, (name, start, end) in enumerate(files):
        insert_spin_file(
            mock_db_session,
            name,
            _utc(*start),
            _utc(*end),
            ingestion_date=_utc(2026, 1, 1, hour),
        )

    spin_files = _spin_inputs(
        job, _utc(2025, 12, 25, 10, 2, 13), _utc(2025, 12, 26, 10, 2, 10)
    )
    assert spin_files == [
        "imap_2025_359_2025_360_10.spin.csv",
        "imap_2025_360_2025_361_10.spin.csv",
    ]


def test_lower_version_covering_the_rest_of_a_repoint_is_used(mock_db_session, job):
    """#1713: a later _01 file covers what the _10 file leaves on day 276."""
    repoint_start, repoint_end = (
        _utc(2026, 10, 2, 10, 2, 13),
        _utc(2026, 10, 3, 10, 2, 10),
    )
    insert_spin_file(
        mock_db_session,
        "imap_2026_274_2026_276_10.spin.csv",
        _utc(2026, 10, 1),
        _utc(2026, 10, 3, 6),
        ingestion_date=_utc(2026, 10, 3, 7),
    )
    with pytest.raises(MissingDependenciesError):
        _spin_inputs(job, repoint_start, repoint_end)

    insert_spin_file(
        mock_db_session,
        "imap_2026_276_2026_277_01.spin.csv",
        _utc(2026, 10, 3, 6),
        _utc(2026, 10, 5),
        ingestion_date=_utc(2026, 10, 5, 1),
    )
    assert _spin_inputs(job, repoint_start, repoint_end) == [
        "imap_2026_274_2026_276_10.spin.csv",
        "imap_2026_276_2026_277_01.spin.csv",
    ]


def test_sensor_triggers_on_new_spin_rows_only(
    mock_db_session, ephemeral_instance, job
):
    """New spin rows in the SPICE table trigger; rows before the cursor do not."""
    spin_dep = next(d for d in job.job_config.inputs if d.data_type == "spin")
    cursor_time = _utc(2026, 6, 1)

    # A row the step 2 backfill re-indexed, with its original ingestion date
    insert_spin_file(
        mock_db_session,
        "imap_2026_007_2026_007_01.spin.csv",
        _utc(2026, 1, 7, 12),
        _utc(2026, 1, 7, 18),
        ingestion_date=_utc(2026, 5, 1),
    )
    # A legacy spin table row alone no longer triggers
    mock_db_session.add(
        models.SpinFiles(
            file_path="imap/spice/spin/imap_2026_009_2026_009_01.spin.csv",
            version="01",
            start_date=datetime.datetime(2026, 1, 9, 12),
            end_date=datetime.datetime(2026, 1, 9, 18),
            ingestion_date=_utc(2026, 6, 3),
            released=True,
        )
    )
    mock_db_session.commit()
    # A newly ingested spin file
    new_ingestion = _utc(2026, 6, 2)
    insert_spin_file(
        mock_db_session,
        "imap_2026_005_2026_005_01.spin.csv",
        _utc(2026, 1, 5, 12),
        _utc(2026, 1, 5, 18),
        ingestion_date=new_ingestion,
    )

    kickoff_sensor = defs.get_sensor_def(
        f"{job.job_config.to_dagster_name()}_kickoff_sensor"
    )
    context = build_sensor_context(
        instance=ephemeral_instance,
        cursor=json.dumps({spin_dep.to_dagster_name(): cursor_time.isoformat()}),
    )
    requests = list(kickoff_sensor(context))

    assert [r.partition_key for r in requests] == [
        "daily_2026-01-05T00:00:00_to_2026-01-06T00:00:00"
    ]
    spin_cursor = json.loads(context.cursor)[spin_dep.to_dagster_name()]
    assert datetime.datetime.fromisoformat(spin_cursor) == new_ingestion


def test_wrapper_returns_nothing_when_no_spin_files():
    """Without require_coverage, no spin files is an empty result, not an error."""
    with patch.object(
        spice.spice_metakernel_api,
        "lambda_handler",
        return_value={"statusCode": 404, "body": "No files found."},
    ):
        assert spice.get_upstream_dependency_inputs_spin(
            _utc(2026, 1, 1), _utc(2026, 1, 2)
        ) == ([], [])


def test_wrapper_raises_on_unexpected_status():
    """A failed query is an error, not a skipped job."""
    with (
        patch.object(
            spice.spice_metakernel_api,
            "lambda_handler",
            return_value={"statusCode": 400, "body": "Invalid file_types"},
        ),
        pytest.raises(RuntimeError, match="400"),
    ):
        spice.get_upstream_dependency_inputs_spin(_utc(2026, 1, 1), _utc(2026, 1, 2))
