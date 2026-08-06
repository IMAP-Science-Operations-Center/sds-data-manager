"""Test spin-data endpoint."""

import datetime
import json
import os
import sys
from pathlib import Path

import pytest
import spiceypy

# Add the project root to the path to allow imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from sds_data_manager.lambda_code.SDSCode.api_lambdas import (
    non_spice_table_api,
    spice_query_api,
)
from sds_data_manager.lambda_code.SDSCode.database.models import (
    RepointFiles,
    SmallForcesFile,
    SpinFiles,
)


@pytest.fixture
def spin_db(session):
    """Create a session with test data for spin files."""
    # Create sample spin file records
    spin_files = [
        SpinFiles(
            file_path="imap/spice/spin/imap_2026_267_2026_268_02.spin.csv",
            start_date=datetime.datetime(2026, 9, 24, 0, 0, 0),
            end_date=datetime.datetime(2026, 9, 25, 0, 0, 0),
            version="02",
            ingestion_date=datetime.datetime(2025, 7, 8, 20, 10, 57, 614857),
        ),
        SpinFiles(
            file_path="imap/spice/spin/imap_2026_268_2026_268_01.spin.csv",
            start_date=datetime.datetime(2026, 9, 25, 0, 0, 0),
            end_date=datetime.datetime(2026, 9, 25, 0, 0, 0),
            version="01",
            ingestion_date=datetime.datetime(2025, 7, 9, 20, 10, 55, 256060),
        ),
        SpinFiles(
            file_path="imap/spice/spin/imap_2026_268_2026_269_01.spin.csv",
            start_date=datetime.datetime(2026, 9, 25, 0, 0, 0),
            end_date=datetime.datetime(2026, 9, 26, 0, 0, 0),
            version="01",
            ingestion_date=datetime.datetime(2025, 7, 10, 20, 10, 57, 774315),
        ),
    ]

    session.add_all(spin_files)
    session.commit()


@pytest.fixture
def repoint_db(session):
    """Create a session with test data for spin files."""
    # Create sample repoint file records
    repoint_files = [
        RepointFiles(
            file_path="imap/spice/repoint/imap_2026_267_2026_268_02.repoint",
            end_date=datetime.datetime(2026, 9, 25, 0, 0, 0),
            version="02",
            ingestion_date=datetime.datetime(2025, 7, 8, 20, 10, 57, 614857),
        ),
        RepointFiles(
            file_path="imap/spice/repoint/imap_2026_268_2026_268_01.repoint",
            end_date=datetime.datetime(2026, 9, 30, 0, 0, 0),
            version="01",
            ingestion_date=datetime.datetime(2025, 7, 9, 20, 10, 55, 256060),
        ),
    ]

    session.add_all(repoint_files)
    session.commit()


@pytest.fixture
def small_forces_db(session):
    """Create a session with test data for small forces files."""
    # Create sample small forces file records
    small_forces_files = [
        SmallForcesFile(
            file_path="imap/spice/small-forces/imap_2025_100_2025_110_hist_01.sff",
            start_date=datetime.datetime(2025, 4, 10, 0, 0, 0),
            end_date=datetime.datetime(2025, 4, 20, 0, 0, 0),
            version="01",
            ingestion_date=datetime.datetime(2025, 4, 5, 10, 0, 0),
        ),
        SmallForcesFile(
            file_path="imap/spice/small-forces/imap_2025_100_2025_110_hist_02.sff",
            start_date=datetime.datetime(2025, 4, 10, 0, 0, 0),
            end_date=datetime.datetime(2025, 4, 20, 0, 0, 0),
            version="02",
            ingestion_date=datetime.datetime(2025, 4, 6, 10, 0, 0),
        ),
    ]

    session.add_all(small_forces_files)
    session.commit()


def test_spin_table_api_date_filters(spin_db):
    """Test query with date filters similar to the API example."""
    event = {
        "queryStringParameters": {
            "start_date": "20260925",
            "end_date": "20260925",
        },
        "rawPath": "/spin-table",
    }
    context = {}

    response = non_spice_table_api.lambda_handler(event, context)

    assert response["statusCode"] == 200
    results = json.loads(response["body"])
    assert isinstance(results, list)

    # Validate the result format
    for result in results:
        assert "file_path" in result
        assert "start_date" in result
        assert "end_date" in result
        assert "version" in result
        assert "ingestion_date" in result
    assert len(results) == 1

    # Check the first result has expected values
    assert (
        results[0]["file_path"] == "imap/spice/spin/imap_2026_268_2026_268_01.spin.csv"
    )
    assert results[0]["start_date"].startswith("2026-09-25")


# def test_spin_table_api_latest_version(spin_db):
#     """Test that 'latest=true' returns only the newest version of each date."""
#     event = {
#         "queryStringParameters": {
#             "latest": "true"
#         }
#     }

#     response = spin_table_api.lambda_handler(event, {})

#     assert response["statusCode"] == 200
#     results = json.loads(response["body"])

#     # Should have two results - the latest version from each day
#     assert len(results) == 2

#     # Verify we got v002 for first date and v001 for second date
#     date_to_version = {result["start_date"]: result["version"] for result in results}
#     assert any(date.startswith("2026-09-25") and version == "v002"
#                 for date, version in date_to_version.items())
#     assert any(date.startswith("2026-09-26") and version == "v001"
#                 for date, version in date_to_version.items())


def test_spin_table_api_invalid_parameter(spin_db):
    """Test error handling for invalid query parameters."""
    event = {
        "queryStringParameters": {"invalid_param": "value"},
        "rawPath": "/spin-table",
    }

    response = non_spice_table_api.lambda_handler(event, {})

    # Check that we get the proper error response
    assert response["statusCode"] == 400
    assert "invalid_param is not a valid query parameter" in response["body"]


def test_spin_table_api_invalid_date_format(spin_db):
    """Test error handling for invalid date formats."""
    event = {
        "queryStringParameters": {"start_date": "2025-09-25"},
        "rawPath": "/spin-table",
    }

    response = non_spice_table_api.lambda_handler(event, {})

    # Check that we get the proper error response
    assert response["statusCode"] == 400
    assert "Invalid value for start_date" in response["body"]


def test_ingestion_date(spin_db):
    """Test that ingestion date query works."""
    event = {
        "queryStringParameters": {"start_ingest_date": "20250710"},
        "rawPath": "/spin-table",
    }

    response = non_spice_table_api.lambda_handler(event, {})

    # Check successful response
    assert response["statusCode"] == 200
    results = json.loads(response["body"])

    # Exactly one result should be returned
    assert len(results) == 1

    # Verify the filtering worked correctly
    for result in results:
        # Results should be for 2026-09-25
        assert result["start_date"].startswith("2026-09-25")
        # Ingestion date should be on or after 2025-07-10
        assert result["ingestion_date"] >= "2025-07-10"

    # Try with end date now
    event = {
        "queryStringParameters": {"end_ingest_date": "20250709"},
        "rawPath": "/spin-table",
    }

    response = non_spice_table_api.lambda_handler(event, {})
    assert response["statusCode"] == 200
    results = json.loads(response["body"])

    # Exactly one result should be returned
    assert len(results) == 1

    for result in results:
        # Ingestion date should be on or after 2025-07-10
        assert result["ingestion_date"] >= "2025-07-08"


def test_repoint_table(repoint_db):
    """Test querying the repointing table."""
    event = {
        "queryStringParameters": {
            "start_date": "20260925",
            "end_date": "20260925",
        },
        "rawPath": "/repoint-table",
    }
    context = {}

    response = non_spice_table_api.lambda_handler(event, context)

    assert response["statusCode"] == 200
    results = json.loads(response["body"])
    assert isinstance(results, list)

    # Validate the result format
    for result in results:
        assert "file_path" in result
        assert "end_date" in result
        assert "version" in result
        assert "ingestion_date" in result
    assert len(results) == 1

    # Check the first result has expected values
    assert (
        results[0]["file_path"]
        == "imap/spice/repoint/imap_2026_267_2026_268_02.repoint"
    )
    assert results[0]["end_date"].startswith("2026-09-25")


def test_small_forces_table(small_forces_db):
    """Test querying the small forces table."""
    event = {
        "queryStringParameters": {
            "start_date": "20250410",
            "end_date": "20250420",
        },
        "rawPath": "/small-forces-table",
    }
    context = {}

    response = non_spice_table_api.lambda_handler(event, context)

    assert response["statusCode"] == 200
    results = json.loads(response["body"])
    assert isinstance(results, list)

    # Validate the result format
    for result in results:
        assert "file_path" in result
        assert "start_date" in result
        assert "end_date" in result
        assert "version" in result
        assert "ingestion_date" in result

    # Both files should be returned since they overlap the date range
    assert len(results) == 2

    # Check results contain expected values
    file_paths = [result["file_path"] for result in results]
    assert [
        "imap/spice/small-forces/imap_2025_100_2025_110_hist_01.sff",
        "imap/spice/small-forces/imap_2025_100_2025_110_hist_02.sff",
    ] == file_paths

    # Verify dates are correct
    for result in results:
        assert result["start_date"].startswith("2025-04-10")
        assert result["end_date"].startswith("2025-04-20")


# Tests when non-SPICE tables are accessed via `spice_query_api`


def test_spin_via_spice_query_api(spin_db):
    """Test spin table query via the spice query API."""
    event = {
        "queryStringParameters": {
            "type": "spin",
            "start_date": "20260925",
            "end_date": "20260925",
        },
    }
    response = spice_query_api.lambda_handler(event, {})

    assert response["statusCode"] == 200
    results = json.loads(response["body"])
    assert len(results) == 1
    assert (
        results[0]["file_path"] == "imap/spice/spin/imap_2026_268_2026_268_01.spin.csv"
    )
    assert results[0]["start_date"].startswith("2026-09-25")


def test_repoint_via_spice_query_api(repoint_db):
    """Test repoint table query via the spice query API."""
    event = {
        "queryStringParameters": {
            "type": "repoint",
            "end_date": "20260925",
        },
    }
    response = spice_query_api.lambda_handler(event, {})

    assert response["statusCode"] == 200
    results = json.loads(response["body"])
    assert len(results) == 1
    assert (
        results[0]["file_path"]
        == "imap/spice/repoint/imap_2026_267_2026_268_02.repoint"
    )
    assert results[0]["end_date"].startswith("2026-09-25")


def test_thruster_via_spice_query_api(small_forces_db):
    """Test small-forces table query via the spice query API."""
    event = {
        "queryStringParameters": {
            "type": "thruster",
            "start_date": "20250410",
            "end_date": "20250420",
        },
    }
    response = spice_query_api.lambda_handler(event, {})

    assert response["statusCode"] == 200
    results = json.loads(response["body"])
    assert len(results) == 2
    file_paths = [r["file_path"] for r in results]
    assert "imap/spice/small-forces/imap_2025_100_2025_110_hist_01.sff" in file_paths
    assert "imap/spice/small-forces/imap_2025_100_2025_110_hist_02.sff" in file_paths


def test_file_name_renamed_to_file_path(spin_db):
    """The query param `file_name` is translated to `file_path` for non-SPICE tables."""
    event = {
        "queryStringParameters": {
            "type": "spin",
            "file_name": "imap/spice/spin/imap_2026_268_2026_268_01.spin.csv",
        },
    }
    response = spice_query_api.lambda_handler(event, {})

    assert response["statusCode"] == 200
    results = json.loads(response["body"])
    assert len(results) == 1
    assert (
        results[0]["file_path"] == "imap/spice/spin/imap_2026_268_2026_268_01.spin.csv"
    )


def test_thruster_start_end_time_via_spice_query_api(small_forces_db):
    """Test small-forces table query using start/end time via the spice query API."""
    tests_path = Path(os.path.abspath(__file__)).parent.parent
    test_spice_data_dir = tests_path / "test-data" / "test_spice_files"
    with spiceypy.KernelPool([str(test_spice_data_dir / "naif0012.tls")]):
        start_time = spiceypy.datetime2et(
            datetime.datetime.strptime("20250410", "%Y%m%d")
        )
        end_time = spiceypy.datetime2et(
            datetime.datetime.strptime("20250420", "%Y%m%d")
        )

        event = {
            "queryStringParameters": {
                "type": "thruster",
                "start_time": f"{start_time}",
                "end_time": f"{end_time}",
            },
        }
        response = spice_query_api.lambda_handler(event, {})

        assert response["statusCode"] == 200
        results = json.loads(response["body"])
        assert len(results) == 2
        file_paths = [r["file_path"] for r in results]
        assert (
            "imap/spice/small-forces/imap_2025_100_2025_110_hist_01.sff" in file_paths
        )
        assert (
            "imap/spice/small-forces/imap_2025_100_2025_110_hist_02.sff" in file_paths
        )
