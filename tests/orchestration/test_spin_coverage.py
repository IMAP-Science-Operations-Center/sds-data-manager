"""Test spin coverage requirements for jobs that consume spin files.

MAG L1D and SWE L2 declare ``require_coverage: true`` on their spin dependency,
so they are skipped until spin files cover the whole day. Other spin consumers
take whatever spin files overlap their window.
"""

import datetime
from collections import namedtuple

import pytest

from sds_data_manager.orchestration import imap_job
from sds_data_manager.orchestration.dagster_utilities import (
    parse_dates_from_partition_key,
)
from sds_data_manager.orchestration.imap_dagster import job_handlers
from sds_data_manager.orchestration.spin import verify_spin_coverage
from sds_data_manager.orchestration.types import DependencyNode
from tests.orchestration.conftest import _insert_spin_file

TARGET_PARTITION = "daily_2026-09-10T00:00:00_to_2026-09-11T00:00:00"
PARTIAL_DAY_SPIN_FILE = "imap_2026_252_2026_253_01.spin"
FULL_DAY_SPIN_FILE = "imap_2026_253_2026_254_01.spin"

SpinRecord = namedtuple("SpinRecord", ["file_path", "start_date", "end_date"])


def _day(month: int, day: int) -> datetime.datetime:
    return datetime.datetime(2026, month, day)


def _job(source: str, data_type: str, descriptor: str):
    """Look up the registered job handler for a product."""
    return next(
        handler
        for handler in job_handlers
        if (
            handler.job_config.source,
            handler.job_config.data_type,
            handler.job_config.descriptor,
        )
        == (source, data_type, descriptor)
    )


@pytest.mark.parametrize("product", [("mag", "l1d", "norm-srf"), ("swe", "l2", "sci")])
def test_mag_l1d_and_swe_l2_require_spin_coverage(mock_db_session, product):
    """Spin inputs are withheld until spin files cover the whole day."""
    job = _job(*product)
    assert job.job_config.spin_input.require_coverage is True
    target_start, target_end = parse_dates_from_partition_key(TARGET_PARTITION)

    _insert_spin_file(
        mock_db_session,
        PARTIAL_DAY_SPIN_FILE,
        start_date=datetime.datetime(2026, 9, 9),
        end_date=datetime.datetime(2026, 9, 10),
    )
    with pytest.raises(imap_job.MissingDependenciesError, match="spin"):
        job.get_spin_files_inputs(mock_db_session, target_start, target_end)

    _insert_spin_file(
        mock_db_session,
        FULL_DAY_SPIN_FILE,
        start_date=datetime.datetime(2026, 9, 10),
        end_date=datetime.datetime(2026, 9, 11),
    )
    spin_files = job.get_spin_files_inputs(mock_db_session, target_start, target_end)
    assert set(spin_files) == {PARTIAL_DAY_SPIN_FILE, FULL_DAY_SPIN_FILE}


def test_require_coverage_defaults_to_false():
    """A spin dependency that omits the flag does not require coverage."""
    job = _job("hi", "l1b", "45sensor-de")
    assert job.job_config.spin_input.require_coverage is False


def test_verify_spin_coverage_longer_file_spans_shorter_ones():
    """A longer file covers the window despite shorter files sorted around it."""
    records = [
        SpinRecord("imap_2026_204_2026_205_01.spin", _day(7, 23), _day(7, 24)),
        SpinRecord("imap_2026_204_2026_206_02.spin", _day(7, 23), _day(7, 25)),
        SpinRecord("imap_2026_205_2026_205_01.spin", _day(7, 24), _day(7, 24)),
        SpinRecord("imap_2026_206_2026_206_02.spin", _day(7, 25), _day(7, 25)),
        SpinRecord("imap_2026_206_2026_207_02.spin", _day(7, 25), _day(7, 26)),
    ]

    assert verify_spin_coverage(records, _day(7, 24), _day(7, 25))


def test_verify_spin_coverage_same_day_file_listed_after_two_day_file():
    """A same-day file sorted after a two-day file does not end the coverage."""
    records = [
        SpinRecord("imap_2026_051_2026_052_01.spin", _day(2, 20), _day(2, 21)),
        SpinRecord("imap_2026_051_2026_051_01.spin", _day(2, 20), _day(2, 20)),
    ]

    assert verify_spin_coverage(records, _day(2, 20), _day(2, 21))


def test_require_coverage_must_be_boolean():
    """A non-boolean require_coverage is rejected."""
    with pytest.raises(ValueError, match="require_coverage"):
        DependencyNode(
            source="spin",
            data_type="spin",
            descriptor="historical",
            require_coverage="true",
        )
