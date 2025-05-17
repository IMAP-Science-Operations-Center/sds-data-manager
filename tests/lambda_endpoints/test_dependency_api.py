"""Test data dependency functions."""

import os
from datetime import datetime
from unittest.mock import patch

import pytest
from imap_data_access.processing_input import (
    AncillaryInput,
    ProcessingInputCollection,
    ScienceInput,
)

from sds_data_manager.lambda_code.SDSCode.database.models import (
    ScienceFiles,
    SpinTable,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import dependency
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency import (
    DependencyConfig,
    get_files,
)
from tests.lambda_endpoints.conftest import (
    _populate_file_catalog,
)

#####################################
# ERROR STATUS CODE TESTS
#####################################


def test_no_dependencies():
    """Test lambda_handler when no dependencies are found."""
    deps = dependency.get_jobs(
        data_source="nonexistent",
        data_type="l0",
        descriptor="raw",
        dependency_type="UPSTREAM",
        relationship="HARD",
    )
    assert deps == []


def test_invalid_dependency_type():
    """Test lambda_handler when invalid dependency type is provided."""
    with pytest.raises(KeyError):
        dependency.get_jobs(
            data_source="jim",
            data_type="l0",
            descriptor="raw",
            dependency_type="INVALID",
            relationship="HARD",
        )


def test_missing_dependency(session):
    """Test that "None" is returned."""
    result = dependency.get_jobs(
        data_source="swe",
        data_type="l1b",
        start_date="20240104",
        end_date="20241204",
        descriptor="sci",
        dependency_type="DOWNSTREAM",
        relationship="HARD",
    )
    assert not result


def test_soft_dependencies(session):
    """Test that the correct soft dependencies are returned."""
    _populate_file_catalog(session)
    dependency_response = dependency.get_jobs(
        data_source="mag",
        data_type="l1c",
        descriptor="norm-mago",
        start_date="20240101",
        end_date="20241201",
        relationship="SOFT_TRIGGER",
        dependency_type="UPSTREAM",
    )
    # There should be two science inputs: one for mag_l1b_burst-mago and
    # mag_l1b_norm-mago
    # Expect ancillary dependencies and science dependencies
    expected_processing_input = ProcessingInputCollection(
        ScienceInput("imap_mag_l1b_norm-mago_20240101_v002.cdf"),
        ScienceInput("imap_mag_l1b_burst-mago_20240101_v001.cdf"),
    )
    assert dependency_response.serialize() == expected_processing_input.serialize()


def test_missing_soft_dependencies(session):
    """Test that the correct soft dependencies are returned."""
    session.add(
        ScienceFiles(
            file_path="/path/to/imap_mag_l1b_norm-mago_20240101_v001.cdf",
            instrument="mag",
            data_level="l1b",
            descriptor="norm-mago",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    )
    session.commit()
    dependency_response = dependency.get_jobs(
        data_source="mag",
        data_type="l1c",
        descriptor="norm-mago",
        start_date="20240101",
        end_date="20241201",
        relationship="SOFT_TRIGGER",
        dependency_type="UPSTREAM",
    )
    # There should be one science input: one for mag_l1b_norm-mago
    # Even though burst-mago is missing.
    # Expect ancillary dependencies and science dependencies
    expected_processing_input = ProcessingInputCollection(
        ScienceInput("imap_mag_l1b_norm-mago_20240101_v001.cdf")
    )
    assert dependency_response.serialize() == expected_processing_input.serialize()


def test_missing_required_params():
    """Test that 400 error is returned."""
    with pytest.raises(
        ValueError,
        match="end_date not found. If 'start_date' is "
        "supplied, 'end_date' is required.",
    ):
        dependency.get_jobs(
            dependency_type="DOWNSTREAM",
            relationship="HARD",
            data_source="swe",
            data_type="l1b",
            descriptor="sci",
            start_date="20240104",
        )


#####################################
# LAMBDA HANDLER TESTS
#####################################
def test_get_downstream_dependencies():
    """Tests get_downstream_dependencies function."""
    dependency_response = dependency.get_jobs(
        data_source="hit",
        data_type="l1a",
        descriptor="counts",
        relationship="HARD",
        dependency_type="DOWNSTREAM",
    )

    expected_complete_dependent = [
        {
            "data_source": "hit",
            "data_type": "l1b",
            "descriptor": "all",
            "relationship": "HARD",
        }
    ]
    assert len(dependency_response) == 1

    assert dependency_response == expected_complete_dependent

    # Add test for getting back ancillary dependency
    dependency_response = dependency.get_jobs(
        data_source="swe",
        data_type="l1b",
        descriptor="sci",
        relationship="HARD",
        dependency_type="UPSTREAM",
    )

    expected_complete_dependent = [
        {
            "data_source": "swe",
            "data_type": "l1a",
            "descriptor": "sci",
            "relationship": "HARD",
        },
        {
            "data_source": "swe",
            "data_type": "ancillary",
            "descriptor": "l1b-in-flight-cal",
            "relationship": "HARD",
        },
        {
            "data_source": "swe",
            "data_type": "ancillary",
            "descriptor": "esa-lut",
            "relationship": "HARD",
        },
        {
            "data_source": "swe",
            "data_type": "ancillary",
            "descriptor": "eu-conversion",
            "relationship": "HARD",
        },
    ]
    assert len(dependency_response) == 4
    assert dependency_response == expected_complete_dependent


def test_get_all_downstream_dependencies_for_relationship():
    """Add test for getting back ancillary dependencies."""
    dependency_response = dependency.get_jobs(
        data_source="mag",
        data_type="l1b",
        descriptor="norm-mago",
        relationship="ALL",
        dependency_type="DOWNSTREAM",
    )
    expected_complete_dependent = [
        {
            "data_source": "mag",
            "data_type": "l1c",
            "descriptor": "norm-mago",
            "relationship": "SOFT_TRIGGER",
        },
    ]
    assert dependency_response == expected_complete_dependent


def test_get_kickoff_jobs():
    """Add test for getting back each instrument pipeline's initial job."""
    dependents = DependencyConfig().kickoff_pipeline_jobs()
    # There are 14 jobs that are HARD downstream dependencies from l0
    assert len(dependents) == 14
    for dep in dependents:
        # Some instruments have l1b jobs that are downstream from l0 (lo and hit).
        assert dep["data_type"] in ["l1a", "l1b", "l1"]


def test_get_upstream_ancillary_trigger(session, caplog):
    """Tests get upstream dependencies with an ancillary trigger source."""
    _populate_file_catalog(session)
    dependency_response = dependency.get_jobs(
        data_source="swe",
        data_type="l1b",
        dependency_type="UPSTREAM",
        start_date="20231230",
        end_date="20240104",
        descriptor="sci",
        relationship="HARD",
    )
    # There are three swe l1a records before 20240104.
    science_in = ScienceInput(
        "imap_swe_l1a_sci_20240101_v010.cdf",
        "imap_swe_l1a_sci_20240102_v001.cdf",
        "imap_swe_l1a_sci_20240103_v001.cdf",
    )
    ancillary_in = [
        AncillaryInput(
            "imap_swe_l1b-in-flight-cal_20230102_v001.cdf",
        ),
        AncillaryInput("imap_swe_esa-lut_20221231_v001.cdf"),
        AncillaryInput("imap_swe_eu-conversion_20221231_v001.cdf"),
    ]
    # Expect ancillary dependencies and science dependencies
    expected_processing_input = ProcessingInputCollection(science_in, *ancillary_in)

    assert dependency_response.serialize() == expected_processing_input.serialize()
    # Move end_date forward by one
    # There are now three valid ancillary in-flight-cal files for this date but the
    # one with the latest start_date is returned.
    dependency_response = dependency.get_jobs(
        data_source="swe",
        data_type="l1b",
        dependency_type="UPSTREAM",
        start_date="20231230",
        end_date="20240105",
        descriptor="sci",
        relationship="HARD",
    )
    ancillary_in = [
        AncillaryInput(
            "imap_swe_l1b-in-flight-cal_20240104_20240106_v002.cdf",
        ),
        AncillaryInput("imap_swe_esa-lut_20221231_v001.cdf"),
        AncillaryInput("imap_swe_eu-conversion_20221231_v001.cdf"),
    ]

    expected_processing_input = ProcessingInputCollection(science_in, *ancillary_in)
    assert dependency_response.serialize() == expected_processing_input.serialize()


@patch.object(dependency.DependencyConfig, "_load_dependencies")
def test_dependency_class(mock_load_dependencies):
    """Test DependencyConfig class."""
    # Set side effect to return value error of product not having
    # valid source, type, and descriptor.
    msg = "Data product must have: (source, type, descriptor)"
    mock_load_dependencies.side_effect = ValueError(msg)

    with pytest.raises(
        ValueError, match="Data product must have: \\(source, type, descriptor\\)"
    ):
        dependency.DependencyConfig()


#####################################
# GET_FILES() TESTS
#####################################
def test_get_primary_science_files(session):
    """Tests the get_file function for science files."""
    _populate_file_catalog(session)

    dep = {"data_source": "mag", "data_type": "l1b", "descriptor": "burst-mago"}
    record = get_files(
        session,
        dependency=dep,
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 1),
    )[0]

    assert record.instrument == "mag"
    assert record.data_level == "l1b"
    assert record.descriptor == "burst-mago"
    assert record.start_date == datetime(2024, 1, 1)
    assert record.version == "v001"

    # Non-existent record should return an empty list
    record = get_files(
        session,
        dependency=dep,
        start_date=datetime(2009, 1, 5),
        end_date=datetime(2009, 1, 5),
    )
    assert record == []

    # Query for file that falls in middle date of range
    dep = {
        "data_source": "swe",
        "data_type": "l1a",
        "descriptor": "sci",
    }
    record = get_files(
        session,
        dependency=dep,
        start_date=datetime(2024, 1, 4),
        end_date=datetime(2024, 1, 7),
    )
    assert len(record) == 1
    assert record[0].instrument == "swe"
    assert record[0].data_level == "l1a"
    assert record[0].descriptor == "sci"
    assert record[0].start_date == datetime(2024, 1, 6)


def test_get_science_files_date_range(session):
    """Tests the get_file function for science files dependent on start_date."""
    _populate_file_catalog(session)
    # Test with larger date_range
    # It should return two swe records
    dep = {"data_source": "swe", "data_type": "l1a", "descriptor": "sci"}
    records_1 = get_files(
        session,
        dependency=dep,
        start_date=datetime(2024, 1, 2),
        end_date=datetime(2024, 1, 3),
    )
    assert len(records_1) == 2


def test_get_ancillary_files(session):
    """Tests the get_file function."""
    _populate_file_catalog(session)

    dep = {
        "data_source": "swe",
        "data_type": "ancillary",
        "descriptor": "l1b-in-flight-cal",
    }
    record = get_files(
        session,
        dependency=dep,
        start_date=datetime(2023, 1, 1),
        end_date=datetime(2023, 1, 1),
    )[0]
    assert record.instrument == "swe"
    assert record.descriptor == "l1b-in-flight-cal"
    assert record.start_date == datetime(2023, 1, 1)
    assert record.version == "v001"

    # Get ancillary file covering range.
    # There are three ancillary files valid for this range.
    record = get_files(
        session,
        dependency=dep,
        start_date=datetime(2024, 1, 2),
        end_date=datetime(2024, 1, 10),
    )
    assert len(record) == 1
    assert record[0].instrument == "swe"
    assert record[0].descriptor == "l1b-in-flight-cal"


def test_get_files_max_version(session):
    """Test get_files returns the max version."""
    _populate_file_catalog(session)
    dep = {"data_source": "swe", "data_type": "l1a", "descriptor": "sci"}
    records = get_files(
        session,
        dependency=dep,
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 3),
    )

    assert len(records) == 3
    for rec in records:
        assert rec.instrument == "swe"
        assert rec.data_level == "l1a"
        assert rec.descriptor == "sci"
    # Make sure the dates and versions are the latest ones
    assert records[0].start_date == datetime(2024, 1, 1)
    assert records[0].version == "v010"

    assert records[1].start_date == datetime(2024, 1, 2)
    assert records[1].version == "v001"

    assert records[2].start_date == datetime(2024, 1, 3)
    assert records[2].version == "v001"


# #####################################
# TESTS SPICE logics
# #####################################


def test_get_latest_repoint_file(s3_client):
    """Test get_latest_repoint_file function."""
    # Write repoint files to the S3 bucket
    bucket_name = "test-data-bucket"
    s3_client.create_bucket(Bucket=bucket_name)
    os.environ["S3_BUCKET"] = bucket_name

    s3_client.put_object(
        Bucket=bucket_name,
        Key="imap/spice/repoint/imap_2025_120_01.repoint.csv",  # 20250430
        Body=b"test",
    )

    # Test with date of the file
    end_date = datetime(2025, 4, 30)
    latest_file = dependency.get_latest_repoint_file(end_date)
    assert latest_file == "imap_2025_120_01.repoint.csv"

    # Add more files to the bucket
    s3_client.put_object(
        Bucket=bucket_name,
        Key="imap/spice/repoint/imap_2025_121_01.repoint.csv",  # 20250501
        Body=b"test",
    )
    s3_client.put_object(
        Bucket=bucket_name,
        Key="imap/spice/repoint/imap_2025_121_02.repoint.csv",  # 20250501
        Body=b"test",
    )

    # Test with date before the first file
    end_date = datetime(2025, 3, 1)
    latest_file = dependency.get_latest_repoint_file(end_date)
    assert latest_file == "imap_2025_121_02.repoint.csv"

    # Test with a date after the latest file
    end_date = datetime(2025, 6, 30)
    latest_file = dependency.get_latest_repoint_file(end_date)
    assert latest_file is None


def test_get_spin_files(session):
    """Test get_spin_files function."""
    # Add spin files to the database
    session.add_all(
        [
            SpinTable(
                file_path="/imap/spice/spin/imap_2025_119_2025_120_01.spin.csv",
                start_date=datetime(2025, 4, 29),
                end_date=datetime(2025, 4, 30),
                version="01",
                ingestion_date=datetime.now(),
            ),
            SpinTable(
                file_path="/imap/spice/spin/imap_2025_120_2025_121_01.spin.csv",
                start_date=datetime(2025, 4, 30),
                end_date=datetime(2025, 5, 1),
                version="01",
                ingestion_date=datetime.now(),
            ),
        ]
    )
    session.commit()

    # Test with overlapping date range
    start_date = datetime(2025, 4, 29)
    end_date = datetime(2025, 4, 30)
    spin_files = dependency.get_spin_files(session, start_date, end_date)
    assert spin_files == [
        "imap_2025_119_2025_120_01.spin.csv",
        "imap_2025_120_2025_121_01.spin.csv",
    ]

    # Test with a date range that does not overlap
    start_date = datetime(2025, 5, 2)
    end_date = datetime(2025, 5, 3)
    spin_files = dependency.get_spin_files(session, start_date, end_date)
    assert spin_files == []

    # Test with one day date range
    start_date = datetime(2025, 4, 29)
    end_date = datetime(2025, 4, 29)
    spin_files = dependency.get_spin_files(session, start_date, end_date)
    assert spin_files == [
        "imap_2025_119_2025_120_01.spin.csv",
    ]


def test_combine_kernel_sources():
    """Test combine_kernel_sources function."""
    # Test with valid SPICE dependencies excluding REPOINT and SPIN
    dependencies = [
        {
            "data_source": "attitude_history",
            "data_type": "spice",
            "descriptor": "historical",
        },
        {
            "data_source": "ephemeris_reconstructed",
            "data_type": "spice",
            "descriptor": "historical",
        },
    ]
    result = dependency.combine_kernel_sources(dependencies)
    assert result == "attitude_history,ephemeris_reconstructed"

    # Test with REPOINT and SPIN dependencies
    dependencies = [
        {"data_source": "repoint", "data_type": "spice", "descriptor": "historical"},
        {"data_source": "spin", "data_type": "spice", "descriptor": "historical"},
        {
            "data_source": "attitude_history",
            "data_type": "spice",
            "descriptor": "historical",
        },
    ]
    result = dependency.combine_kernel_sources(dependencies)
    assert result == "attitude_history"

    # Test with an empty dependency list
    dependencies = []
    result = dependency.combine_kernel_sources(dependencies)
    assert result == ""

    # Test with only REPOINT and SPIN dependencies
    dependencies = [
        {"data_source": "repoint", "data_type": "spice", "descriptor": "historical"},
        {"data_source": "spin", "data_type": "spice", "descriptor": "historical"},
    ]
    result = dependency.combine_kernel_sources(dependencies)
    assert result == ""

    # Test with only REPOINT dependency
    dependencies = [
        {"data_source": "repoint", "data_type": "spice", "descriptor": "historical"},
        {
            "data_source": "attitude_history",
            "data_type": "spice",
            "descriptor": "historical",
        },
    ]
    result = dependency.combine_kernel_sources(dependencies)
    assert result == "attitude_history"

    # Test with only SPIN dependency
    dependencies = [
        {"data_source": "spin", "data_type": "spice", "descriptor": "historical"},
        {
            "data_source": "attitude_history",
            "data_type": "spice",
            "descriptor": "historical",
        },
    ]
    result = dependency.combine_kernel_sources(dependencies)
    assert result == "attitude_history"

    # Pass invalid SPICE file types
    dependencies = [
        {
            "data_source": "invalid_file_type",
            "data_type": "spice",
            "descriptor": "historical",
        },
    ]
    result = dependency.combine_kernel_sources(dependencies)
    assert result == ""
    # Pass instrument name as data_source
    dependencies = [
        {"data_source": "idex", "data_type": "spice", "descriptor": "historical"},
    ]
    result = dependency.combine_kernel_sources(dependencies)
    assert result == ""
