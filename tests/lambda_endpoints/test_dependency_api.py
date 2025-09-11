"""Test data dependency functions."""

import base64
from datetime import datetime
from unittest.mock import patch

import imap_data_access
import pytest
from imap_data_access.processing_input import (
    AncillaryInput,
    ProcessingInputCollection,
    ScienceInput,
)

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.database.models import (
    AncillaryFiles,
    RepointFiles,
    ScienceFiles,
    SpinFiles,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import dependency
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency import (
    DependencyConfig,
    calculate_crid,
    get_files,
    matching_crids_exist,
)
from tests.lambda_endpoints.conftest import (
    _static_spice_files,
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
    _static_spice_files(session)
    records = [
        ScienceFiles(
            file_path="/path/to/imap_mag_l1b_burst-mago_20240101_v001.cdf",
            instrument="mag",
            data_level="l1b",
            descriptor="burst-mago",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_mag_l1b_norm-mago_20240101_v002.cdf",
            instrument="mag",
            data_level="l1b",
            descriptor="norm-mago",
            start_date=datetime(2024, 1, 1),
            version="v002",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()

    dependency_response = dependency.get_jobs(
        data_source="mag",
        data_type="l1c",
        descriptor="norm-mago",
        start_date="20240101",
        end_date="20241201",
        relationship="SOFT_TRIGGER",
        dependency_type="UPSTREAM",
        calculate_crids=True,
    )
    # There should be two science inputs: one for mag_l1b_burst-mago and
    # mag_l1b_norm-mago
    # Expect ancillary dependencies and science dependencies
    expected_processing_input = ProcessingInputCollection(
        ScienceInput("imap_mag_l1b_norm-mago_20240101_v002.cdf"),
        ScienceInput("imap_mag_l1b_burst-mago_20240101_v001.cdf"),
    )
    assert dependency_response.serialize() == expected_processing_input.serialize()
    # Assert both upstream science files have a crid associated with it now.
    l1b_norm_mago = session.get(
        ScienceFiles, "/path/to/imap_mag_l1b_norm-mago_20240101_v002.cdf"
    )
    l1b_burst_mago = session.get(
        ScienceFiles, "/path/to/imap_mag_l1b_burst-mago_20240101_v001.cdf"
    )
    assert l1b_norm_mago.crid
    assert l1b_burst_mago.crid


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


def test_get_jobs_spice(session):
    """Test that spice files are returned as dependencies."""
    # Glows l1a all depends on time kernels and one glows l0 raw file
    session.add(
        ScienceFiles(
            file_path="/path/to/imap_glows_l0_raw_20240101_v001.pkts",
            instrument="glows",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        )
    )
    session.commit()
    # Get upstream files for glows l1a all. This should return None because although
    # the glows l0 raw file exists, the spice files do not exist in the database.
    dependency_response = dependency.get_jobs(
        data_source="glows",
        data_type="l1a",
        descriptor="all",
        start_date="20240101",
        end_date="20241231",
        relationship="ALL",
        dependency_type="UPSTREAM",
        get_spice=True,  # Since we are checking spice, get_jobs will return None
    )
    assert not dependency_response

    # Make the same call but skip checking spice files. This should return the glows
    # l0 raw file.
    dependency_response = dependency.get_jobs(
        data_source="glows",
        data_type="l1a",
        descriptor="all",
        start_date="20240101",
        end_date="20240101",
        relationship="ALL",
        dependency_type="UPSTREAM",
        get_spice=False,  # Since we skip spice, get_jobs will return the l0 raw file
    )
    expected_processing_input = ProcessingInputCollection(
        ScienceInput("imap_glows_l0_raw_20240101_v001.pkts"),
    )
    assert dependency_response.serialize() == expected_processing_input.serialize()


#####################################
# LAMBDA HANDLER TESTS
#####################################
def test_get_downstream_dependencies():
    """Tests get_downstream_dependencies function."""
    dependency_response = dependency.get_jobs(
        data_source="hit",
        data_type="l1a",
        descriptor="counts-sectored",
        relationship="HARD",
        dependency_type="DOWNSTREAM",
    )

    expected_complete_dependent = [
        {
            "data_source": "hit",
            "data_type": "l1b",
            "descriptor": "sectored-rates",
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


def test_get_downstream_dependencies_for_all_relationships():
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
    _static_spice_files(session)

    records = [
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240101_v010.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            extension="cdf",
            start_date=datetime(2024, 1, 1),
            version="v010",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240102_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            extension="cdf",
            start_date=datetime(2024, 1, 2),
            version="v001",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240103_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            extension="cdf",
            start_date=datetime(2024, 1, 3),
            version="v001",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_l1b-in-flight-cal_20230102_v001.csv",
            instrument="swe",
            descriptor="l1b-in-flight-cal",
            extension="csv",
            start_date=datetime(2023, 1, 2),
            version="v001",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_esa-lut_20221231_v001.csv",
            instrument="swe",
            descriptor="esa-lut",
            extension="csv",
            start_date=datetime(2022, 12, 31),
            version="v001",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_eu-conversion_20221231_v001.csv",
            instrument="swe",
            descriptor="eu-conversion",
            extension="csv",
            start_date=datetime(2022, 12, 31),
            version="v001",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()

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
            "imap_swe_l1b-in-flight-cal_20230102_v001.csv",
        ),
        AncillaryInput("imap_swe_esa-lut_20221231_v001.csv"),
        AncillaryInput("imap_swe_eu-conversion_20221231_v001.csv"),
    ]
    # Expect ancillary dependencies and science dependencies
    expected_processing_input = ProcessingInputCollection(science_in, *ancillary_in)

    assert dependency_response.serialize() == expected_processing_input.serialize()

    record = [
        AncillaryFiles(
            file_path="path/to/imap_swe_l1b-in-flight-cal_20240104_20240106_v002.csv",
            instrument="swe",
            descriptor="l1b-in-flight-cal",
            extension="csv",
            start_date=datetime(2024, 1, 4),
            version="v002",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        )
    ]
    session.add_all(record)
    session.commit()
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
            "imap_swe_l1b-in-flight-cal_20240104_20240106_v002.csv",
        ),
        AncillaryInput("imap_swe_esa-lut_20221231_v001.csv"),
        AncillaryInput("imap_swe_eu-conversion_20221231_v001.csv"),
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
    _static_spice_files(session)

    records = [
        ScienceFiles(
            file_path="path/to/imap_mag_l1b_burst-mago_20240101_v001.cdf",
            instrument="mag",
            data_level="l1b",
            descriptor="burst-mago",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="path/to/imap_swe_l1a_sci_20240106_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 1, 6),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()

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
    _static_spice_files(session)
    records = [
        ScienceFiles(
            file_path="path/to/imap_swe_l1a_sci_20240102_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 1, 2),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="path/to/imap_swe_l1a_sci_20240103_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 1, 3),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()
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
    _static_spice_files(session)
    records = [
        AncillaryFiles(
            file_path="path/to/imap_swe_l1b-in-flight-cal_20230101_v001.csv",
            instrument="swe",
            descriptor="l1b-in-flight-cal",
            extension="csv",
            start_date=datetime(2023, 1, 1),
            version="v001",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()

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
    _static_spice_files(session)
    dep = {"data_source": "swe", "data_type": "l1a", "descriptor": "sci"}

    records = [
        ScienceFiles(
            file_path="path/to/imap_swe_l1a_sci_20240101_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="path/to/imap_swe_l1a_sci_20240101_v010.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 1, 1),
            version="v010",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="path/to/imap_swe_l1a_sci_20240102_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 1, 2),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()

    science_files = get_files(
        session,
        dependency=dep,
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 3),
    )

    assert len(science_files) == 2
    for rec in science_files:
        assert rec.instrument == "swe"
        assert rec.data_level == "l1a"
        assert rec.descriptor == "sci"
    # Make sure the dates and versions are the latest ones
    assert science_files[0].start_date == datetime(2024, 1, 1)
    assert science_files[0].version == "v010"
    assert science_files[1].start_date == datetime(2024, 1, 2)
    assert science_files[1].version == "v001"


# #####################################
# TESTS SPICE logics
# #####################################


def test_get_latest_repoint_file(session):
    """Test get_latest_repoint_file function."""
    records = [
        RepointFiles(
            file_path="imap/spice/repoint/imap_2025_120_01.repoint.csv",
            end_date=datetime(2025, 4, 30),
            version="01",
            ingestion_date=datetime.now(),
        )
    ]
    session.add_all(records)
    session.commit()

    # Test with date of the file
    end_date = datetime(2025, 4, 30)
    latest_file = dependency.get_latest_repoint_file(end_date)
    assert latest_file == "imap_2025_120_01.repoint.csv"

    records = [
        RepointFiles(
            file_path="imap/spice/repoint/imap_2025_121_01.repoint.csv",
            end_date=datetime(2025, 5, 1),
            version="01",
            ingestion_date=datetime.now(),
        ),
        RepointFiles(
            file_path="imap/spice/repoint/imap_2025_121_02.repoint.csv",
            end_date=datetime(2025, 5, 1),
            version="02",
            ingestion_date=datetime.now(),
        ),
    ]
    session.add_all(records)
    session.commit()

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
            SpinFiles(
                file_path="/imap/spice/spin/imap_2025_119_2025_120_01.spin.csv",
                start_date=datetime(2025, 4, 29),
                end_date=datetime(2025, 4, 30),
                version="01",
                ingestion_date=datetime.now(),
            ),
            SpinFiles(
                file_path="/imap/spice/spin/imap_2025_120_2025_121_01.spin.csv",
                start_date=datetime(2025, 4, 30),
                end_date=datetime(2025, 5, 1),
                version="01",
                ingestion_date=datetime.now(),
            ),
            SpinFiles(
                file_path="/imap/spice/spin/imap_2026_267_2026_268_01.spin.csv",
                start_date=datetime(2026, 9, 23),
                end_date=datetime(2026, 9, 24),
                version="01",
                ingestion_date=datetime.now(),
            ),
            SpinFiles(
                file_path="/imap/spice/spin/imap_2026_267_2026_268_02.spin.csv",
                start_date=datetime(2026, 9, 23),
                end_date=datetime(2026, 9, 24),
                version="02",
                ingestion_date=datetime.now(),
            ),
            SpinFiles(
                file_path="/imap/spice/spin/imap_2026_268_2026_268_01.spin.csv",
                start_date=datetime(2026, 9, 24),
                end_date=datetime(2026, 9, 24),
                version="01",
                ingestion_date=datetime.now(),
            ),
            SpinFiles(
                file_path="/imap/spice/spin/imap_2026_268_2026_268_02.spin.csv",
                start_date=datetime(2026, 9, 24),
                end_date=datetime(2026, 9, 24),
                version="02",
                ingestion_date=datetime.now(),
            ),
            SpinFiles(
                file_path="/imap/spice/spin/imap_2026_268_2026_269_01.spin.csv",
                start_date=datetime(2026, 9, 24),
                end_date=datetime(2026, 9, 25),
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

    start_date = datetime(2026, 9, 25)
    end_date = datetime(2026, 9, 25)
    spin_files = dependency.get_spin_files(session, start_date, end_date)
    assert spin_files == ["imap_2026_268_2026_269_01.spin.csv"]

    # Test with overlapping date range and latest version
    start_date = datetime(2026, 9, 24)
    end_date = datetime(2026, 9, 24)
    spin_files = dependency.get_spin_files(session, start_date, end_date)
    assert spin_files == [
        "imap_2026_267_2026_268_02.spin.csv",
        "imap_2026_268_2026_268_02.spin.csv",
        "imap_2026_268_2026_269_01.spin.csv",
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


def test_get_all_nodes():
    """Test get_all_nodes function."""
    #  DependencyConfig object
    dependency_config = DependencyConfig()
    # Call the get_all_nodes method
    all_nodes = dependency_config.get_all_nodes()
    assert len(all_nodes) > 200
    for instrument in imap_data_access.VALID_INSTRUMENTS:
        if instrument != "ialirt":
            assert (instrument, "l0", "raw") in all_nodes


def test_get_cadence_jobs():
    """Test get_cadence_jobs function."""
    #  DependencyConfig object
    dependency_config = DependencyConfig()
    # Call the get_all_nodes method
    all_nodes = dependency_config.get_cadence_jobs("1yr")
    for node in all_nodes:
        assert node["data_type"] == "l2"
        assert node["descriptor"].split("-")[-1] in ["1yr"]


#####################################
# CRID TESTS
#####################################


def test_calculate_crid(session):
    """Test CRID calculation."""
    _static_spice_files(session)
    records = [
        # File to build CRID for
        ScienceFiles(
            file_path="/path/to/imap_swe_l1b_sci_20240101_v001.cdf",
            instrument="swe",
            data_level="l1b",
            descriptor="sci",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            crid="8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4",
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l0_raw_20240101_v001.pkts",
            instrument="swe",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240101_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 1, 1),
            version="v010",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        # Adding a downstream swe l1b file that depends on the science file above
        AncillaryFiles(
            file_path="/path/to/imap_swe_l1b-in-flight-cal_20230102_v001.cdf",
            instrument="swe",
            descriptor="l1b-in-flight-cal",
            start_date=datetime(2023, 1, 2),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_esa-lut_20221231_v001.cdf",
            instrument="swe",
            descriptor="esa-lut",
            start_date=datetime(2022, 12, 31),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_eu-conversion_20221231_v001.cdf",
            instrument="swe",
            descriptor="eu-conversion",
            start_date=datetime(2022, 12, 31),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()

    record = (
        session.query(models.ScienceFiles)
        .filter(
            models.ScienceFiles.file_path
            == "/path/to/imap_swe_l1b_sci_20240101_v001.cdf"
        )
        .first()
    )
    crid = calculate_crid(session, record)
    # The CRID associated with a file is made up of the filepath and the
    # Upstream file versions numbers packed into 2 bytes and sorted by the filename
    # imap_swe_l1b_sci_20240101_v001.cdf has a total of 4 upstream dependency files:
    # - imap_swe_l1a_sci_20240101_v001.cdf.cdf
    # - imap_swe_l0_raw_20240101_v001.pkts
    # - imap_swe_l1a_sci_20240101_v010.cdf
    # - imap_swe_esa-lut_20221231_v001.cdf
    # - imap_swe_eu-conversion_20221231_v001.cdf

    # the upstream versions should be in order of the filenames alphabetically
    upstream_versions = b"".join([v.to_bytes(2, "big") for v in [1, 1, 1, 10, 1]])
    expected_crid = base64.a85encode(record.file_path.encode() + upstream_versions)
    assert expected_crid.decode("ascii") == crid

    # Test that we can decode the CRID to see what upstream versions were used.
    assert base64.a85decode(crid)


def test_calculate_crid_l0(session):
    """Test CRID calculation."""
    _static_spice_files(session)

    records = [
        ScienceFiles(
            file_path="/path/to/imap_swe_l0_raw_20240101_v001.pkts",
            instrument="swe",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()

    record = (
        session.query(models.ScienceFiles)
        .filter(
            models.ScienceFiles.file_path
            == "/path/to/imap_swe_l0_raw_20240101_v001.pkts"
        )
        .first()
    )
    crid = calculate_crid(session, record)
    # L0 files have no upstream dependencies, so the crid is just a hash of the filepath
    expected_crid = base64.a85encode(record.file_path.encode()).decode("ascii")
    assert expected_crid == crid


def test_matching_crid(session):
    """Test CRID check."""
    _static_spice_files(session)
    records = [
        ScienceFiles(
            file_path="/path/to/imap_swe_l0_raw_20240101_v001.pkts",
            instrument="swe",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240101_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 1, 1),
            version="v010",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        # Adding a downstream swe l1b file that depends on the science file above
        AncillaryFiles(
            file_path="/path/to/imap_swe_l1b-in-flight-cal_20230102_v001.cdf",
            instrument="swe",
            descriptor="l1b-in-flight-cal",
            start_date=datetime(2023, 1, 2),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_esa-lut_20221231_v001.cdf",
            instrument="swe",
            descriptor="esa-lut",
            start_date=datetime(2022, 12, 31),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_eu-conversion_20221231_v001.cdf",
            instrument="swe",
            descriptor="eu-conversion",
            start_date=datetime(2022, 12, 31),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()
    records = [
        # Add a science file with a CRID that will not match the calculated CRID
        ScienceFiles(
            file_path="/path/to/imap_swe_l1b_sci_20240102_v001.cdf",
            instrument="swe",
            data_level="l1b",
            descriptor="sci",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            crid="testing",
        ),
    ]
    assert not matching_crids_exist(session, records)
    # Update the CRID of the science file to match the calculated CRID
    records[
        0
    ].crid = "05t?ABJ4IG05593E*m[1ARB7.@UF1dBjWVL1,L[>0J[!Y0JG46@q90O!<<-#!<<H,!<"
    assert matching_crids_exist(session, records)


def test_new_crid(session):
    """Test that a new CRID is generated for a file with no CRID."""
    _static_spice_files(session)
    filename = "/path/to/imap_swe_l1b_sci_20240102_v001.cdf"
    records = [
        # Add a science file with no CRID
        ScienceFiles(
            file_path=filename,
            instrument="swe",
            data_level="l1b",
            descriptor="sci",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()
    matching_crids_exist(session, records)
    # Now query for that record.
    record = (
        session.query(models.ScienceFiles)
        .filter(models.ScienceFiles.file_path == filename)
        .first()
    )
    # Assert that the record now has a CRID associated with it.
    assert record.crid
