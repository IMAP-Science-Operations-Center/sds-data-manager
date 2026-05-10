"""Tests for the idex l0 indexer lambda."""

import datetime
import os
from unittest.mock import patch

import numpy as np
import pytest

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import idex_l0_indexer
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.idex_l0_indexer import (
    compute_idex_l0_event_times,
    compute_idex_l0_start_dates,
)
from sds_data_manager.lambda_code.SDSCode.spice_utilities import furnish_best_spice_file
from tests.lambda_endpoints.test_spice_indexer_lambda import (
    _insert_test_file,
    put_local_file_in_bucket,
)

IDEX_L0_TEST_FILE = "imap/idex/l0/2024/01/imap_idex_l0_raw_20240101_v001.pkts"


@pytest.fixture
def setup_data(session, s3_client):
    """Create test data."""
    current_path = os.path.dirname(os.path.abspath(__file__))
    one_level_up = os.path.abspath(os.path.join(current_path, ".."))
    test_spice_data_dir = os.path.join(one_level_up, "test-data", "test_spice_files")
    test_idex_data_dir = os.path.join(one_level_up, "test-data", "test_idex_l0_files")

    # Insert leapsecond spice kernel
    lsk_test_path = os.path.join(test_spice_data_dir, "naif0012.tls")
    put_local_file_in_bucket(
        s3_client,
        "imap/spice/lsk/naif0012.tls",
        lsk_test_path,
    )
    _insert_test_file(
        session,
        "naif0012.tls",
        "imap/spice/lsk/naif0012.tls",
        [[0, 1000000000]],  # Dummy intervals for testing
    )

    # Insert spacecraft clock spice kernel
    sclk_test_path = os.path.join(test_spice_data_dir, "imap_sclk_0012.tsc")
    put_local_file_in_bucket(
        s3_client,
        "imap/spice/sclk/imap_sclk_0012.tsc",
        sclk_test_path,
    )
    _insert_test_file(
        session,
        "imap_sclk_0012.tsc",
        "imap/spice/sclk/imap_sclk_0012.tsc",
        [[0, 1000000000]],  # Dummy intervals for testing
    )
    # Add the IDEX l0 file
    pkts_file_test_path = os.path.join(
        test_idex_data_dir, "imap_idex_l0_raw_20240101_v001.pkts"
    )
    put_local_file_in_bucket(
        s3_client,
        IDEX_L0_TEST_FILE,
        pkts_file_test_path,
    )

    def download_side_effect(path):
        if path.endswith("naif0012.tls"):
            return lsk_test_path
        elif path.endswith("imap_sclk_0012.tsc"):
            return sclk_test_path
        else:
            raise ValueError(f"Unexpected download path: {path}")

    with patch(
        "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.download_from_s3",
        side_effect=download_side_effect,
    ):
        yield


def test_s3_sci_event(session, s3_client, events_client, setup_data):
    """Test s3 event."""
    event = {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": "2024-01-16T17:35:08Z",
        "detail": {
            "version": "0",
            "bucket": {"name": "test-data-bucket"},
            "object": {
                "key": IDEX_L0_TEST_FILE,
                "reason": "PutObject",
            },
        },
    }
    # Test for good event
    returned_value = idex_l0_indexer.lambda_handler(event=event, context={})
    assert returned_value["statusCode"] == 200

    # Check that data was written to database by lambda
    result = session.query(models.IDEXL0Files).all()
    assert len(result) == 1
    assert (
        result[0].file_path
        == "imap/idex/l0/2024/01/imap_idex_l0_raw_20240101_v001.pkts"
    )
    assert result[0].version == "v001"
    assert result[0].start_date == datetime.datetime(2025, 3, 31, 0, 0)


def test_compute_idex_l0_event_times(session, s3_client, events_client, setup_data):
    """Test compute_idex_l0_event_times."""
    # Furnish time kernels
    furnish_best_spice_file("leapseconds")
    furnish_best_spice_file("spacecraft_clock")
    event_times = compute_idex_l0_event_times(IDEX_L0_TEST_FILE)

    # assert that the length of the event_times is
    assert len(event_times) == 2
    # Check the event time values
    assert event_times[0] == np.datetime64("2025-04-02T15:25:22.887764812")
    assert event_times[1] == np.datetime64("2025-04-02T15:27:14.887764812")


def test_compute_idex_l0_start_dates(session, s3_client, events_client, setup_data):
    """Test compute_idex_l0_start_dates."""
    # Furnish time kernels
    furnish_best_spice_file("leapseconds")
    furnish_best_spice_file("spacecraft_clock")

    mock_event_times = np.array(
        [
            np.datetime64("2025-04-02T15:25:22.887764812"),
            np.datetime64("2026-04-02T15:25:22.887764812"),
            # The last two dates share the same window
            np.datetime64("2026-04-30T15:25:22.887764812"),
            np.datetime64("2026-05-08T15:25:22.887764812"),
        ]
    ).astype(np.datetime64)
    with patch(
        "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.idex_l0_indexer.compute_idex_l0_event_times",
        return_value=mock_event_times,
    ):
        start_dates = compute_idex_l0_start_dates(IDEX_L0_TEST_FILE)
        # The mock event times are over 3 different 10-day windows, so we expect 3
        # unique start dates
        assert len(start_dates) == 3
        assert start_dates[0] == np.datetime64("2025-03-31T00:00:00.000000000")
        assert start_dates[1] == np.datetime64("2026-03-31T00:00:00.000000000")
        assert start_dates[2] == np.datetime64("2026-04-30T00:00:00.000000000")


def test_compute_idex_l0_start_dates_invalid_time(
    session, s3_client, events_client, setup_data
):
    """Test compute_idex_l0_start_dates when there is an invalid time."""
    # Furnish time kernels
    furnish_best_spice_file("leapseconds")
    furnish_best_spice_file("spacecraft_clock")

    mock_event_times = np.array(
        [
            np.datetime64(
                "2010-01-01T15:25:22.887764812"
            ),  # Invalid time (before launch)
            np.datetime64("2026-04-02T15:25:22.887764812"),
        ]
    ).astype(np.datetime64)
    with patch(
        "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.idex_l0_indexer."
        "compute_idex_l0_event_times",
        return_value=mock_event_times,
    ):
        with pytest.raises(
            ValueError,
            match="Event times fall outside the mission window range",
        ):
            compute_idex_l0_start_dates(IDEX_L0_TEST_FILE)


def test_compute_idex_l0_start_dates_no_events(
    session, s3_client, events_client, setup_data
):
    """Test compute_idex_l0_start_dates when there are no event times."""
    # Furnish time kernels
    furnish_best_spice_file("leapseconds")
    furnish_best_spice_file("spacecraft_clock")

    mock_event_times = np.array([]).astype(np.datetime64)
    with patch(
        "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas."
        "idex_l0_indexer.compute_idex_l0_event_times",
        return_value=mock_event_times,
    ):
        start_dates = compute_idex_l0_start_dates(IDEX_L0_TEST_FILE)
        # it should return an empty list and not error out
        assert start_dates == []
