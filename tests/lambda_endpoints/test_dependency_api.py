"""Test data dependency functions."""

import base64
from collections import namedtuple
from datetime import datetime
from os.path import basename
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
    ProcessingJob,
    RepointFiles,
    ScienceFiles,
    SPICEFiles,
    SpinFiles,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import dependency
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency import (
    DependencyConfig,
    _get_inprogress_dates,
    _get_inprogress_repoints,
    calculate_crid,
    get_files,
    get_jobs,
    get_n_nearest_files_by_date,
    get_n_nearest_files_by_repoint,
    get_upstream_dependency_inputs,
    matching_crids_exist,
    verify_science_coverage,
    verify_spin_coverage,
)
from tests.lambda_endpoints.conftest import (
    _static_spice_files,
)

#####################################
# FIXTURES
#####################################


@pytest.fixture
def hi_l1b_de_repoint_files(session):
    """Fixture that creates Hi L1B 45sensor-de files for repoints 1-5."""
    _static_spice_files(session)
    records = [
        ScienceFiles(
            file_path="path/to/imap_hi_l1b_45sensor-de_20240101-repoint00001_v001.cdf",
            instrument="hi",
            data_level="l1b",
            descriptor="45sensor-de",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            repointing=1,
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="path/to/imap_hi_l1b_45sensor-de_20240101-repoint00001_v002.cdf",
            instrument="hi",
            data_level="l1b",
            descriptor="45sensor-de",
            start_date=datetime(2024, 1, 1),
            version="v002",
            extension="cdf",
            repointing=1,
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="path/to/imap_hi_l1b_45sensor-de_20240102-repoint00002_v001.cdf",
            instrument="hi",
            data_level="l1b",
            descriptor="45sensor-de",
            start_date=datetime(2024, 1, 2),
            version="v001",
            extension="cdf",
            repointing=2,
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="path/to/imap_hi_l1b_45sensor-de_20240103-repoint00003_v001.cdf",
            instrument="hi",
            data_level="l1b",
            descriptor="45sensor-de",
            start_date=datetime(2024, 1, 3),
            version="v001",
            extension="cdf",
            repointing=3,
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="path/to/imap_hi_l1b_45sensor-de_20240104-repoint00004_v001.cdf",
            instrument="hi",
            data_level="l1b",
            descriptor="45sensor-de",
            start_date=datetime(2024, 1, 4),
            version="v001",
            extension="cdf",
            repointing=4,
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="path/to/imap_hi_l1b_45sensor-de_20240105-repoint00005_v001.cdf",
            instrument="hi",
            data_level="l1b",
            descriptor="45sensor-de",
            start_date=datetime(2024, 1, 5),
            version="v001",
            extension="cdf",
            repointing=5,
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()
    return records


#####################################
# ERROR STATUS CODE TESTS
#####################################


def test_no_dependencies():
    """Test lambda_handler when no dependencies are found."""
    deps = dependency.get_dependencies(
        ("nonexistent", "l0", "raw"),
        dependency_type="UPSTREAM",
        relationship="HARD",
    )
    assert deps == []


def test_invalid_dependency_type():
    """Test lambda_handler when invalid dependency type is provided."""
    with pytest.raises(KeyError):
        dependency.get_dependencies(
            ("jim", "l0", "raw"),
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
        TypeError, match=r"get_jobs\(\) missing 1 required positional argument:"
    ):
        dependency.get_jobs(
            dependency_type="DOWNSTREAM",
            relationship="HARD",
            data_source="swe",
            data_type="l1b",
            descriptor="sci",
            start_date="20240104",
        )


def test_get_jobs_repoint(session):
    """Test that jobs with the correct repoint are returned as dependencies."""
    # Add two identical glows l0 raw files except for repointing number
    session.add_all(
        [
            ScienceFiles(
                file_path="/path/to/imap_glows_l0_raw_20240101-repoint00047_v001.pkts",
                instrument="glows",
                data_level="l0",
                descriptor="raw",
                start_date=datetime(2024, 1, 1),
                version="v001",
                extension="pkts",
                repointing=47,
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            ),
            ScienceFiles(
                file_path="/path/to/imap_glows_l0_raw_20240101-repoint00048_v001.pkts",
                instrument="glows",
                data_level="l0",
                descriptor="raw",
                start_date=datetime(2024, 1, 1),
                version="v001",
                extension="pkts",
                repointing=48,
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            ),
        ]
    )
    session.commit()
    # Get upstream files for glows l1a all. This should return both glows l0 raw files
    dependency_response = dependency.get_jobs(
        data_source="glows",
        data_type="l1a",
        descriptor="all",
        start_date="20240101",
        end_date="20240101",
        relationship="ALL",
        dependency_type="UPSTREAM",
        get_spice=False,
    )
    # Without specifying repointing, both files are returned
    assert len(dependency_response.get_file_paths("glows", descriptor="raw")) == 2
    dependency_response = dependency.get_jobs(
        data_source="glows",
        data_type="l1a",
        descriptor="all",
        start_date="20240101",
        end_date="20240101",
        repoint=48,
        relationship="ALL",
        dependency_type="UPSTREAM",
        get_spice=False,
    )
    # With specifying repointing, only one file is returned
    glows_l0_files = dependency_response.get_file_paths("glows", descriptor="raw")
    assert len(glows_l0_files) == 1
    assert (
        basename(glows_l0_files[0])
        == "imap_glows_l0_raw_20240101-repoint00048_v001.pkts"
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
    dependency_response = dependency.get_dependencies(
        ("hit", "l1a", "counts-sectored"),
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
    dependency_response = dependency.get_dependencies(
        ("swe", "l1b", "sci"),
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
    dependency_response = dependency.get_dependencies(
        ("mag", "l1b", "norm-mago"),
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


def test_get_files_with_single_repoint(hi_l1b_de_repoint_files, session):
    """Test get_files with a single repoint parameter."""
    dep = {"data_source": "hi", "data_type": "l1b", "descriptor": "45sensor-de"}

    # Test with single repoint (int)
    science_files = get_files(
        session,
        dependency=dep,
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 3),
        repoint=2,
    )

    assert len(science_files) == 1
    assert science_files[0].repointing == 2
    assert science_files[0].start_date == datetime(2024, 1, 2)


def test_get_files_with_list_of_repoints(hi_l1b_de_repoint_files, session):
    """Test get_files with a list of repoints parameter."""
    # Use the fixture which sets up files for repoints 1-5
    dep = {"data_source": "hi", "data_type": "l1b", "descriptor": "45sensor-de"}

    # Test with list of repoints
    science_files = get_files(
        session,
        dependency=dep,
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 4),
        repoint=[1, 2, 3],
    )

    # Should return 3 files with repoints 1, 2, 3
    assert len(science_files) == 3
    repoints_found = {f.repointing for f in science_files}
    assert repoints_found == {1, 2, 3}


def test_get_files_with_list_of_repoints_max_version(hi_l1b_de_repoint_files, session):
    """Test get_files with list of repoints returns max version per repoint."""
    dep = {"data_source": "hi", "data_type": "l1b", "descriptor": "45sensor-de"}

    # Test with list of repoints - should return max version for each repoint
    science_files = get_files(
        session,
        dependency=dep,
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 2),
        repoint=[1, 2],
    )

    # Should return 2 files: repoint 1 with v002 and repoint 2 with v001
    assert len(science_files) == 2

    # Find the file for repoint 1 and check it's the max version
    repoint1_file = next(f for f in science_files if f.repointing == 1)
    assert repoint1_file.version == "v002"

    # Find the file for repoint 2
    repoint2_file = next(f for f in science_files if f.repointing == 2)
    assert repoint2_file.version == "v001"


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency.get_dependencies",
    return_value=[
        {
            "data_source": "hi",
            "data_type": "l1b",
            "descriptor": "45sensor-de",
            "relationship": "HARD",
        }
    ],
)
def test_get_jobs_hi_goodtimes_multi_repoint(
    mock_get_dependencies, hi_l1b_de_repoint_files, pointing_table_entries, monkeypatch
):
    """Test get_jobs handling for Hi Goodtimes with multi-repoint dependencies.

    Tests that when requesting upstream dependencies for a Hi L1C Goodtimes job,
    the function returns L1B DE files from N repoints total (target + N-1 nearest).
    Also requires that pointing T+N-1 exists.
    """
    # Monkeypatch to use a smaller number for testing
    # With N=3, we get target + 2 nearest = 3 total repoints
    monkeypatch.setattr(dependency, "HI_GOODTIMES_NUM_NEAREST_REPOINTS", 3)

    # Call get_jobs for Hi L1C Goodtimes with repoint 3
    # With NUM_NEAREST=3, this should query for 3 total repoints (target + 2 nearest)
    # Available repoints from fixture: 1, 2, 3, 4, 5
    # Target is 3, nearest 2 are: 2 and 4 (distance 1 each)
    # So result should include repoints 2, 3, 4
    result = get_jobs(
        dependency_type="UPSTREAM",
        relationship="HARD",
        data_source="hi",
        data_type="l1b",
        descriptor="45sensor-goodtimes",
        start_date="20240101",
        end_date="20240105",
        repoint=3,
        calculate_crids=False,
        get_spice=False,
    )

    # Verify that the result contains files from multiple repoints
    assert result is not None
    assert isinstance(result, ProcessingInputCollection)

    # Check that we have L1B DE files from N repoints total
    hi_l1b_files = result.get_file_paths("hi", descriptor="45sensor-de")

    # Should have 3 files: target (3) plus 2 nearest (2 and 4)
    assert len(hi_l1b_files) == 3

    # Verify the files include the target repoint
    result_repoints = set(
        [fp.repointing for fp in result.processing_input[0].imap_file_paths]
    )
    assert 3 in result_repoints  # Target repoint must be included
    # And we should have 3 repoints total (target + 2 nearest)
    assert len(result_repoints) == 3


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency.get_dependencies",
    return_value=[
        {
            "data_source": "hi",
            "data_type": "l1b",
            "descriptor": "45sensor-de",
            "relationship": "HARD",
        }
    ],
)
def test_get_jobs_hi_goodtimes_skips_when_inprogress_nearby(
    mock_get_dependencies,
    hi_l1b_de_repoint_files,
    pointing_table_entries,
    session,
    monkeypatch,
):
    """Test get_jobs skips Hi Goodtimes when N nearest have INPROGRESS L1B DE jobs.

    When finding the N nearest repoints for a Hi Goodtimes job, if any of those
    nearest repoints have INPROGRESS L1B DE jobs (not actual data), the job
    should be skipped because when those jobs complete they will re-trigger.
    """
    # Monkeypatch to use a smaller number for testing
    monkeypatch.setattr(dependency, "HI_GOODTIMES_NUM_NEAREST_REPOINTS", 3)

    # Create an INPROGRESS L1B DE job for repoint 2
    # With existing files at repoints 1, 2, 3, 4, 5 and target repoint 3,
    # repoint 2 would be one of the 3 nearest (2, 4, 1 or 2, 4, 5)
    inprogress_job = ProcessingJob(
        status=models.Status.INPROGRESS,
        instrument="hi",
        data_level="l1b",
        descriptor="45sensor-de",
        start_date=datetime(2024, 1, 2),
        version="v001",
        repointing=2,
    )
    session.add(inprogress_job)
    session.commit()

    # Call get_jobs for Hi L1C Goodtimes with repoint 3
    # Since repoint 2 is among the N nearest and has an INPROGRESS job,
    # this should return None
    result = get_jobs(
        dependency_type="UPSTREAM",
        relationship="HARD",
        data_source="hi",
        data_type="l1b",
        descriptor="45sensor-goodtimes",
        start_date="20240101",
        end_date="20240105",
        repoint=3,
        calculate_crids=False,
        get_spice=False,
    )

    # Should return None because repoint 2 is INPROGRESS
    assert result is None


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency.get_dependencies",
    return_value=[
        {
            "data_source": "hi",
            "data_type": "l1b",
            "descriptor": "45sensor-de",
            "relationship": "HARD",
        }
    ],
)
def test_get_jobs_hi_goodtimes_proceeds_when_inprogress_not_nearby(
    mock_get_dependencies,
    hi_l1b_de_repoint_files,
    pointing_table_entries,
    session,
    monkeypatch,
):
    """Test get_jobs proceeds when INPROGRESS jobs are not among N nearest.

    If INPROGRESS jobs exist but are not among the N nearest repoints,
    the job should proceed normally.
    """
    # Monkeypatch to use a smaller number for testing
    monkeypatch.setattr(dependency, "HI_GOODTIMES_NUM_NEAREST_REPOINTS", 2)

    # Create an INPROGRESS L1B DE job for repoint 10 (far from target)
    # With NUM_NEAREST=2 and target=3, nearest are 2, 4 (or 4, 2)
    # Repoint 10 is not among them
    inprogress_job = ProcessingJob(
        status=models.Status.INPROGRESS,
        instrument="hi",
        data_level="l1b",
        descriptor="45sensor-de",
        start_date=datetime(2024, 1, 10),
        version="v001",
        repointing=10,
    )
    session.add(inprogress_job)
    session.commit()

    # Call get_jobs for Hi L1C Goodtimes with repoint 3
    result = get_jobs(
        dependency_type="UPSTREAM",
        relationship="HARD",
        data_source="hi",
        data_type="l1b",
        descriptor="45sensor-goodtimes",
        start_date="20240101",
        end_date="20240105",
        repoint=3,
        calculate_crids=False,
        get_spice=False,
    )

    # Should return results since INPROGRESS job is not among N nearest
    assert result is not None
    assert isinstance(result, ProcessingInputCollection)


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency.get_dependencies",
    return_value=[
        {
            "data_source": "hi",
            "data_type": "l1b",
            "descriptor": "45sensor-de",
            "relationship": "HARD",
        }
    ],
)
def test_get_jobs_hi_goodtimes_only_future_repoints(
    mock_get_dependencies, hi_l1b_de_repoint_files, pointing_table_entries, monkeypatch
):
    """Test get_jobs for Hi Goodtimes when target is at start (only future repoints).

    When the target repoint is at the beginning of available data, only future
    repoints exist as neighbors. The function should still work correctly.
    """
    # Monkeypatch to use a smaller number for testing
    # With N=3, we get target + 2 nearest = 3 total repoints
    monkeypatch.setattr(dependency, "HI_GOODTIMES_NUM_NEAREST_REPOINTS", 3)

    # Call get_jobs for Hi L1C Goodtimes with repoint 1 (first available)
    # Available repoints from fixture: 1, 2, 3, 4, 5
    # Target is 1, nearest 2 are: 2 and 3 (only future repoints)
    result = get_jobs(
        dependency_type="UPSTREAM",
        relationship="HARD",
        data_source="hi",
        data_type="l1b",
        descriptor="45sensor-goodtimes",
        start_date="20240101",
        end_date="20240105",
        repoint=1,
        calculate_crids=False,
        get_spice=False,
    )

    assert result is not None
    assert isinstance(result, ProcessingInputCollection)

    # Check that we have L1B DE files from N repoints total
    hi_l1b_files = result.get_file_paths("hi", descriptor="45sensor-de")

    # Should have 3 files: target (1) plus 2 nearest (2 and 3)
    assert len(hi_l1b_files) == 3

    # Verify the repoints included
    result_repoints = {
        fp.repointing for fp in result.processing_input[0].imap_file_paths
    }
    assert result_repoints == {1, 2, 3}


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency.get_dependencies",
    return_value=[
        {
            "data_source": "hi",
            "data_type": "l1b",
            "descriptor": "45sensor-de",
            "relationship": "HARD",
        }
    ],
)
def test_get_jobs_hi_goodtimes_only_past_repoints(
    mock_get_dependencies, hi_l1b_de_repoint_files, pointing_table_entries, monkeypatch
):
    """Test get_jobs for Hi Goodtimes when target is at end (only past repoints).

    When the target repoint is at the end of available data, only past
    repoints exist as neighbors. The function should still work correctly.
    """
    # Monkeypatch to use a smaller number for testing
    monkeypatch.setattr(dependency, "HI_GOODTIMES_NUM_NEAREST_REPOINTS", 3)

    # Call get_jobs for Hi L1C Goodtimes with repoint 5 (last available)
    # Available repoints from fixture: 1, 2, 3, 4, 5
    # Target is 5, nearest 2 are: 4 and 3 (only past repoints)
    result = get_jobs(
        dependency_type="UPSTREAM",
        relationship="HARD",
        data_source="hi",
        data_type="l1b",
        descriptor="45sensor-goodtimes",
        start_date="20240101",
        end_date="20240105",
        repoint=5,
        calculate_crids=False,
        get_spice=False,
    )

    assert result is not None
    assert isinstance(result, ProcessingInputCollection)

    # Check that we have L1B DE files from N repoints total
    hi_l1b_files = result.get_file_paths("hi", descriptor="45sensor-de")

    # Should have 3 files: target (5) plus 2 nearest (4 and 3)
    assert len(hi_l1b_files) == 3

    # Verify the repoints included
    result_repoints = {
        fp.repointing for fp in result.processing_input[0].imap_file_paths
    }
    assert result_repoints == {3, 4, 5}


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
    spin_records = dependency.get_spin_files(session, start_date, end_date)
    spin_files = [basename(record.file_path) for record in spin_records]
    assert spin_files == [
        "imap_2025_119_2025_120_01.spin.csv",
        "imap_2025_120_2025_121_01.spin.csv",
    ]

    # Test with a date range that does not overlap
    start_date = datetime(2025, 5, 2)
    end_date = datetime(2025, 5, 3)
    spin_records = dependency.get_spin_files(session, start_date, end_date)
    assert spin_records == []

    # Test with one day date range
    start_date = datetime(2025, 4, 29)
    end_date = datetime(2025, 4, 29)
    spin_records = dependency.get_spin_files(session, start_date, end_date)
    spin_files = [basename(record.file_path) for record in spin_records]
    assert spin_files == [
        "imap_2025_119_2025_120_01.spin.csv",
    ]

    start_date = datetime(2026, 9, 25)
    end_date = datetime(2026, 9, 25)
    spin_records = dependency.get_spin_files(session, start_date, end_date)
    spin_files = [basename(record.file_path) for record in spin_records]
    assert spin_files == ["imap_2026_268_2026_269_01.spin.csv"]

    # Test with overlapping date range and latest version
    start_date = datetime(2026, 9, 24)
    end_date = datetime(2026, 9, 24)
    spin_records = dependency.get_spin_files(session, start_date, end_date)
    spin_files = [basename(record.file_path) for record in spin_records]
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


def test_get_spice_for_ena(session):
    """Tests that the correct spice data is returned for an ena instrument."""
    records = [
        SPICEFiles(
            file_path="path/to/imap_2025_289_2025_290_001.ah.bc",
            file_name="imap_2025_289_2025_290_001.ah.bc",
            ingestion_date=datetime.strptime(
                "2025-10-17 21:05:14.000000 +00:00", "%Y-%m-%d %H:%M:%S.%f %z"
            ),
            file_root="imap_2025_289_2025_290_.ah.bc",
            kernel_type="attitude_history",
            min_date_j2000=813869293.1500088,
            max_date_j2000=813959293.0681704,
            file_intervals_j2000=[[813869293.1500088, 813959293.0681704]],
            min_date_datetime=datetime.strptime(
                "2025-10-16 06:47:03.967637 +00:00", "%Y-%m-%d %H:%M:%S.%f %z"
            ),
            max_date_datetime=datetime.strptime(
                "2025-10-17 07:47:03.885793 +00:00", "%Y-%m-%d %H:%M:%S.%f %z"
            ),
            file_intervals_datetime=[
                ["2025-10-16T06:47:03.967637+00:00", "2025-10-17T07:47:03.885793+00:00"]
            ],
            min_date_sclk="1/0498293225:00000",
            max_date_sclk="1/0498383225:00000",
            file_intervals_sclk=[["1/0498293225:00000", "1/0498383225:00000"]],
            lsk_kernel="",
            sclk_kernel="",
            version=1,
        ),
        SPICEFiles(
            file_path="path/to/imap_2025_290_2025_290_001.ah.bc",
            file_name="imap_2025_290_2025_290_001.ah.bc",
            ingestion_date=datetime.strptime(
                "2025-10-17 21:05:14.000000 +00:00", "%Y-%m-%d %H:%M:%S.%f %z"
            ),
            file_root="imap_2025_290_2025_290_.ah.bc",
            kernel_type="attitude_history",
            min_date_j2000=813955694.071443,
            max_date_j2000=813998895.0321598,
            file_intervals_j2000=[[813955694.071443, 813998895.0321598]],
            min_date_datetime=datetime.strptime(
                "2025-10-17 06:47:04.889065 +00:00", "%Y-%m-%d %H:%M:%S.%f %z"
            ),
            max_date_datetime=datetime.strptime(
                "2025-10-17 18:47:05.849779 +00:00", "%Y-%m-%d %H:%M:%S.%f %z"
            ),
            file_intervals_datetime=[
                ["2025-10-17T06:47:04.889065+00:00", "2025-10-17T18:47:05.849779+00:00"]
            ],
            min_date_sclk="1/0498379626:00000",
            max_date_sclk="1/0498422827:00000",
            file_intervals_sclk=[["1/0498379626:00000", "1/0498422827:00000"]],
            lsk_kernel="",
            sclk_kernel="",
            version=1,
        ),
    ]
    session.add_all(records)

    session.commit()
    # For ENA instruments, the start and end dates are set from the pointing start
    # and end times and are floored in batch_starter.py. This means the pointing end
    # may get "cut off." Therefore, in get_upstream_dependency_inputs in dependency.py
    # for ENA instruments that have a repointing number, we add one day to the end date
    # to ensure all necessary SPICE files are retrieved.
    dependencies = {
        "data_source": "attitude_history",
        "data_type": "spice",
        "descriptor": "historical",
    }
    # For ENA instruments, the start date is not always equal to the end date because
    # a pointing may happen over multiple days.
    start_date = datetime(2025, 10, 16)
    end_date = datetime(2025, 10, 17)
    processing_inputs = get_upstream_dependency_inputs(
        [dependencies],
        start_date=start_date,
        end_date=end_date,
        repoint=1,
        calculate_crids=False,
        get_spice=True,
    )
    # We should expect 2 SPICE files for the date range above.
    assert len(processing_inputs.get_file_paths()) == 2


#####################################
# COVERAGE VERIFICATION TESTS
#####################################
SpinRecord = namedtuple("SpinRecord", ["file_path", "start_date", "end_date"])


class TestVerifySpinCoverage:
    """Test coverage for verify_spin_coverage."""

    def test_verify_spin_coverage_complete(self):
        """Test verify_spin_coverage with complete coverage."""
        # Create mock spin records with complete coverage (overlapping on May 12)
        records = [
            SpinRecord(
                "spin_file1.txt",
                datetime(2024, 5, 10),
                datetime(2024, 5, 12),
            ),
            SpinRecord(
                "spin_file2.txt",
                datetime(2024, 5, 12),
                datetime(2024, 5, 15),
            ),
        ]

        coverage_ok = verify_spin_coverage(
            records, datetime(2024, 5, 10), datetime(2024, 5, 15)
        )

        assert coverage_ok is True

    def test_verify_spin_coverage_gap_at_start(self):
        """Test verify_spin_coverage with gap at the beginning."""
        # First record starts after the requested start_date
        records = [
            SpinRecord(
                "spin_file1.txt",
                datetime(2024, 5, 12),
                datetime(2024, 5, 15),
            ),
        ]

        coverage_ok = verify_spin_coverage(
            records, datetime(2024, 5, 10), datetime(2024, 5, 15)
        )

        assert coverage_ok is False

    def test_verify_spin_coverage_gap_in_middle(self):
        """Test verify_spin_coverage with gap between records."""
        # Gap between records (12th to 14th is missing)
        records = [
            SpinRecord(
                "spin_file1.txt",
                datetime(2024, 5, 10),
                datetime(2024, 5, 11),
            ),
            SpinRecord(
                "spin_file2.txt",
                datetime(2024, 5, 15),
                datetime(2024, 5, 17),
            ),
        ]

        coverage_ok = verify_spin_coverage(
            records, datetime(2024, 5, 10), datetime(2024, 5, 17)
        )

        assert coverage_ok is False

    def test_verify_spin_coverage_gap_at_end(self):
        """Test verify_spin_coverage with gap at the end."""
        # Last record ends before the requested end_date
        records = [
            SpinRecord(
                "spin_file1.txt",
                datetime(2024, 5, 10),
                datetime(2024, 5, 13),
            ),
        ]

        coverage_ok = verify_spin_coverage(
            records, datetime(2024, 5, 10), datetime(2024, 5, 15)
        )

        assert coverage_ok is False

    def test_verify_spin_coverage_overlapping_ranges(self):
        """Test verify_spin_coverage with overlapping ranges (should pass)."""
        # Records with overlapping coverage
        records = [
            SpinRecord(
                "spin_file1.txt",
                datetime(2024, 5, 10),
                datetime(2024, 5, 13),
            ),
            SpinRecord(
                "spin_file2.txt",
                datetime(2024, 5, 12),
                datetime(2024, 5, 15),
            ),
        ]

        coverage_ok = verify_spin_coverage(
            records, datetime(2024, 5, 10), datetime(2024, 5, 15)
        )

        assert coverage_ok is True


ScienceRecord = namedtuple("ScienceRecord", ["file_path", "start_date", "repointing"])


class TestVerifyScienceCoverage:
    """Test coverage for verify_science_coverage."""

    def test_verify_science_coverage_complete(self):
        """Test verify_science_coverage with all dates covered."""
        # All dates from 10th to 13th are covered
        records = [
            ScienceRecord("file1.cdf", datetime(2024, 5, 10), None),
            ScienceRecord("file2.cdf", datetime(2024, 5, 11), None),
            ScienceRecord("file3.cdf", datetime(2024, 5, 12), None),
            ScienceRecord("file4.cdf", datetime(2024, 5, 13), None),
        ]

        dependency = {
            "data_source": "swe",
            "data_type": "l1a",
            "descriptor": "sci",
        }

        coverage_ok = verify_science_coverage(
            records, datetime(2024, 5, 10), datetime(2024, 5, 13), dependency
        )

        assert coverage_ok is True

    def test_verify_science_coverage_missing_dates(self):
        """Test verify_science_coverage with missing dates."""
        # Missing dates: 11th and 13th
        records = [
            ScienceRecord("file1.cdf", datetime(2024, 5, 10), None),
            ScienceRecord("file2.cdf", datetime(2024, 5, 12), None),
        ]

        dependency = {
            "data_source": "mag",
            "data_type": "l1b",
            "descriptor": "burst-mago",
        }

        coverage_ok = verify_science_coverage(
            records, datetime(2024, 5, 10), datetime(2024, 5, 13), dependency
        )

        assert coverage_ok is False

    def test_verify_science_coverage_single_date(self):
        """Test verify_science_coverage when start_date equals end_date."""
        records = [
            ScienceRecord("file1.cdf", datetime(2024, 5, 10), None),
        ]

        dependency = {
            "data_source": "hit",
            "data_type": "l1a",
            "descriptor": "sci",
        }

        coverage_ok = verify_science_coverage(
            records, datetime(2024, 5, 10), datetime(2024, 5, 10), dependency
        )

        assert coverage_ok is True

    def test_verify_science_coverage_with_repoint(self):
        """Test verify_science_coverage with single repoint (complete coverage)."""
        # Records with repoint 42
        records = [
            ScienceRecord("file1.cdf", datetime(2024, 5, 10), 42),
        ]

        dependency = {
            "data_source": "hi",
            "data_type": "l1a",
            "descriptor": "sci",
        }

        # When repoint is provided, only check that repoint exists (not dates)
        coverage_ok = verify_science_coverage(
            records,
            datetime(2024, 5, 10),
            datetime(2024, 5, 12),
            dependency,
            repoint=42,
        )

        assert coverage_ok is True

    def test_verify_science_coverage_with_repoint_missing(self):
        """Test verify_science_coverage with single repoint (missing)."""
        # Records with repoint 40, but we're looking for 42
        records = [
            ScienceRecord("file1.cdf", datetime(2024, 5, 10), 40),
        ]

        dependency = {
            "data_source": "hi",
            "data_type": "l1a",
            "descriptor": "sci",
        }

        coverage_ok = verify_science_coverage(
            records,
            datetime(2024, 5, 10),
            datetime(2024, 5, 12),
            dependency,
            repoint=42,
        )

        assert coverage_ok is False

    def test_verify_science_coverage_with_repoint_list(self):
        """Test verify_science_coverage with list of repoints (complete coverage)."""
        # Records with repoints 40, 41, 42
        records = [
            ScienceRecord("file1.cdf", datetime(2024, 5, 10), 40),
            ScienceRecord("file2.cdf", datetime(2024, 5, 11), 41),
            ScienceRecord("file3.cdf", datetime(2024, 5, 12), 42),
        ]

        dependency = {
            "data_source": "hi",
            "data_type": "l1a",
            "descriptor": "sci",
        }

        # When repoint list is provided, check that all repoints exist
        coverage_ok = verify_science_coverage(
            records,
            datetime(2024, 5, 10),
            datetime(2024, 5, 12),
            dependency,
            repoint=[40, 41, 42],
        )

        assert coverage_ok is True

    def test_verify_science_coverage_with_repoint_list_missing(self):
        """Test verify_science_coverage with list of repoints (missing some)."""
        # Records with repoints 40 and 42 (missing 41)
        records = [
            ScienceRecord("file1.cdf", datetime(2024, 5, 10), 40),
            ScienceRecord("file2.cdf", datetime(2024, 5, 12), 42),
        ]

        dependency = {
            "data_source": "hi",
            "data_type": "l1a",
            "descriptor": "sci",
        }

        coverage_ok = verify_science_coverage(
            records,
            datetime(2024, 5, 10),
            datetime(2024, 5, 12),
            dependency,
            repoint=[40, 41, 42],
        )

        assert coverage_ok is False

    def test_verify_science_coverage_many_missing_dates(self):
        """Test verify_science_coverage with many missing dates (summary format)."""
        # Only 2 dates out of 10
        records = [
            ScienceRecord("file1.cdf", datetime(2024, 5, 10), None),
            ScienceRecord("file2.cdf", datetime(2024, 5, 19), None),
        ]

        dependency = {
            "data_source": "swe",
            "data_type": "l1a",
            "descriptor": "sci",
        }

        coverage_ok = verify_science_coverage(
            records, datetime(2024, 5, 10), datetime(2024, 5, 19), dependency
        )

        assert coverage_ok is False


#####################################
# REQUIRE_COVERAGE INTEGRATION TESTS
#####################################


def test_require_coverage_spin_incomplete(session):
    """Test that incomplete spin coverage blocks processing if coverage required."""
    # Create spin records with a gap in coverage
    # May 10 = day 131, May 11 = day 132, May 13 = day 134, May 15 = day 136
    spin_records = [
        SpinFiles(
            file_path="/path/to/imap_2024_131_2024_132_01.spin.csv",
            start_date=datetime(2024, 5, 10),
            end_date=datetime(2024, 5, 11),
            version="01",
            ingestion_date=datetime(2024, 5, 12),
        ),
        SpinFiles(
            file_path="/path/to/imap_2024_134_2024_136_01.spin.csv",
            start_date=datetime(2024, 5, 13),  # Gap: missing May 12
            end_date=datetime(2024, 5, 15),
            version="01",
            ingestion_date=datetime(2024, 5, 16),
        ),
    ]
    session.add_all(spin_records)
    session.commit()

    dependencies = [{"data_source": "spin", "data_type": "spice", "descriptor": ""}]

    # With require_coverage=True, should return None due to gap
    result = get_upstream_dependency_inputs(
        dependencies,
        start_date=datetime(2024, 5, 10),
        end_date=datetime(2024, 5, 15),
        require_coverage=True,
    )

    assert result is None


def test_require_coverage_spin_complete(session):
    """Test that complete spin coverage succeeds when require_coverage=True."""
    # Create spin records with complete coverage (no gaps)
    # May 10 = day 131, May 12 = day 133, May 15 = day 136
    spin_records = [
        SpinFiles(
            file_path="/path/to/imap_2024_131_2024_133_01.spin.csv",
            start_date=datetime(2024, 5, 10),
            end_date=datetime(2024, 5, 12),
            version="01",
            ingestion_date=datetime(2024, 5, 12),
        ),
        SpinFiles(
            file_path="/path/to/imap_2024_133_2024_136_01.spin.csv",
            start_date=datetime(2024, 5, 12),  # Continuous coverage
            end_date=datetime(2024, 5, 15),
            version="01",
            ingestion_date=datetime(2024, 5, 16),
        ),
    ]
    session.add_all(spin_records)
    session.commit()

    dependencies = [{"data_source": "spin", "data_type": "spice", "descriptor": ""}]

    # With require_coverage=True and complete coverage, should succeed
    result = get_upstream_dependency_inputs(
        dependencies,
        start_date=datetime(2024, 5, 10),
        end_date=datetime(2024, 5, 15),
        require_coverage=True,
    )

    assert result is not None
    assert len(result.get_file_paths()) == 2


def test_require_coverage_science_incomplete(session):
    """Test incomplete science coverage blocks processing if coverage required."""
    _static_spice_files(session)

    # Create science files with missing dates (gap in coverage)
    science_records = [
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240510_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 5, 10),
            version="v001",
            extension="cdf",
            ingestion_date=datetime(2024, 5, 12),
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240512_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 5, 12),  # Missing May 11
            version="v001",
            extension="cdf",
            ingestion_date=datetime(2024, 5, 13),
        ),
    ]
    session.add_all(science_records)
    session.commit()

    dependencies = [
        {
            "data_source": "swe",
            "data_type": "l1a",
            "descriptor": "sci",
            "relationship": "HARD",
        }
    ]

    # With require_coverage=True, should return None due to missing date
    result = get_upstream_dependency_inputs(
        dependencies,
        start_date=datetime(2024, 5, 10),
        end_date=datetime(2024, 5, 12),
        require_coverage=True,
        get_spice=False,
    )

    assert result is None


def test_require_coverage_science_complete(session):
    """Test that complete science coverage succeeds when require_coverage=True."""
    _static_spice_files(session)

    # Create science files with complete daily coverage
    science_records = [
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240510_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 5, 10),
            version="v001",
            extension="cdf",
            ingestion_date=datetime(2024, 5, 12),
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240511_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 5, 11),
            version="v001",
            extension="cdf",
            ingestion_date=datetime(2024, 5, 12),
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240512_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 5, 12),
            version="v001",
            extension="cdf",
            ingestion_date=datetime(2024, 5, 13),
        ),
    ]
    session.add_all(science_records)
    session.commit()

    dependencies = [
        {
            "data_source": "swe",
            "data_type": "l1a",
            "descriptor": "sci",
            "relationship": "HARD",
        }
    ]

    # With require_coverage=True and complete coverage, should succeed
    result = get_upstream_dependency_inputs(
        dependencies,
        start_date=datetime(2024, 5, 10),
        end_date=datetime(2024, 5, 12),
        require_coverage=True,
        get_spice=False,
    )

    assert result is not None
    assert len(result.get_file_paths()) == 3


def test_require_coverage_repoint_incomplete(session):
    """Test incomplete repoint coverage blocks processing if coverage required."""
    _static_spice_files(session)

    # Create science files with repoint 40, but we'll request repoint 41 (missing)
    science_records = [
        ScienceFiles(
            file_path="/path/to/imap_hi_l1a_sci_20240510-repoint00040_v001.cdf",
            instrument="hi",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 5, 10),
            repointing=40,
            version="v001",
            extension="cdf",
            ingestion_date=datetime(2024, 5, 12),
        ),
        ScienceFiles(
            file_path="/path/to/imap_hi_l1a_sci_20240511-repoint00040_v001.cdf",
            instrument="hi",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 5, 11),
            repointing=40,
            version="v001",
            extension="cdf",
            ingestion_date=datetime(2024, 5, 12),
        ),
    ]
    session.add_all(science_records)
    session.commit()

    dependencies = [
        {
            "data_source": "hi",
            "data_type": "l1a",
            "descriptor": "sci",
            "relationship": "HARD",
        }
    ]

    # With require_coverage=True and repoint=41,
    # should return None (no records for repoint 41)
    result = get_upstream_dependency_inputs(
        dependencies,
        start_date=datetime(2024, 5, 10),
        end_date=datetime(2024, 5, 12),
        repoint=41,
        require_coverage=True,
        get_spice=False,
    )

    assert result is None


def test_require_coverage_repoint_complete(session):
    """Test that complete repoint coverage succeeds when require_coverage=True."""
    _static_spice_files(session)

    # Create science files with repoint 40
    # (at least one file with the requested repoint)
    science_records = [
        ScienceFiles(
            file_path="/path/to/imap_hi_l1a_sci_20240510-repoint00040_v001.cdf",
            instrument="hi",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 5, 10),
            repointing=40,
            version="v001",
            extension="cdf",
            ingestion_date=datetime(2024, 5, 12),
        ),
        ScienceFiles(
            file_path="/path/to/imap_hi_l1a_sci_20240511-repoint00040_v001.cdf",
            instrument="hi",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 5, 11),
            repointing=40,
            version="v001",
            extension="cdf",
            ingestion_date=datetime(2024, 5, 12),
        ),
        ScienceFiles(
            file_path="/path/to/imap_hi_l1a_sci_20240512-repoint00040_v001.cdf",
            instrument="hi",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 5, 12),
            repointing=40,
            version="v001",
            extension="cdf",
            ingestion_date=datetime(2024, 5, 13),
        ),
    ]
    session.add_all(science_records)
    session.commit()

    dependencies = [
        {
            "data_source": "hi",
            "data_type": "l1a",
            "descriptor": "sci",
            "relationship": "HARD",
        }
    ]

    # With require_coverage=True and repoint=40 existing in records, should succeed
    result = get_upstream_dependency_inputs(
        dependencies,
        start_date=datetime(2024, 5, 10),
        end_date=datetime(2024, 5, 12),
        repoint=40,
        require_coverage=True,
        get_spice=False,
    )

    assert result is not None
    assert len(result.get_file_paths()) == 3


def test_require_coverage_false_allows_gaps(session):
    """Test that gaps are allowed when require_coverage=False (default behavior)."""
    _static_spice_files(session)

    # Create science files with gap (missing May 11)
    science_records = [
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240510_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 5, 10),
            version="v001",
            extension="cdf",
            ingestion_date=datetime(2024, 5, 12),
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20240512_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 5, 12),  # Missing May 11
            version="v001",
            extension="cdf",
            ingestion_date=datetime(2024, 5, 13),
        ),
    ]
    session.add_all(science_records)
    session.commit()

    dependencies = [
        {
            "data_source": "swe",
            "data_type": "l1a",
            "descriptor": "sci",
            "relationship": "HARD",
        }
    ]

    # With require_coverage=False (default), gaps should be allowed
    result = get_upstream_dependency_inputs(
        dependencies,
        start_date=datetime(2024, 5, 10),
        end_date=datetime(2024, 5, 12),
        require_coverage=False,
        get_spice=False,
    )

    assert result is not None
    assert len(result.get_file_paths()) == 2


#####################################
# GET_N_NEAREST_FILES TESTS
#####################################


@pytest.fixture
def hi_l1b_de_files_with_gaps(session):
    """Fixture that creates Hi L1B 45sensor-de files with gaps in repoints."""
    _static_spice_files(session)
    # Create files for repoints 10, 11, 12, 13, 14, 15, 20, 21, 22 (gap between 15-20)
    records = []
    for rp in [10, 11, 12, 13, 14, 15, 20, 21, 22]:
        records.append(
            ScienceFiles(
                file_path=f"path/to/imap_hi_l1b_45sensor-de_20240101-repoint{rp:05d}_v001.cdf",
                instrument="hi",
                data_level="l1b",
                descriptor="45sensor-de",
                start_date=datetime(2024, 1, 1),
                version="v001",
                extension="cdf",
                repointing=rp,
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            )
        )
    session.add_all(records)
    session.commit()
    return records


@pytest.fixture
def swe_l1a_files_with_gaps(session):
    """Fixture that creates SWE L1A sci files with gaps in dates."""
    _static_spice_files(session)
    # Create files for dates with a gap (Jan 1, 2, 3, 5, 6, 7 - missing Jan 4)
    records = []
    for day in [1, 2, 3, 5, 6, 7]:
        records.append(
            ScienceFiles(
                file_path=f"path/to/imap_swe_l1a_sci_2024010{day}_v001.cdf",
                instrument="swe",
                data_level="l1a",
                descriptor="sci",
                start_date=datetime(2024, 1, day),
                version="v001",
                extension="cdf",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            )
        )
    session.add_all(records)
    session.commit()
    return records


class TestGetNNearestFiles:
    """Test coverage for get_n_nearest_files functions."""

    def test_get_n_nearest_files_repoint_basic(
        self, hi_l1b_de_files_with_gaps, session
    ):
        """Test basic repoint case - finding 4 nearest to repoint 15."""
        dep = {"data_source": "hi", "data_type": "l1b", "descriptor": "45sensor-de"}

        records = get_n_nearest_files_by_repoint(
            session,
            dependency=dep,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
            num_nearest=4,
            repoint=15,
        )

        # Should get 4 nearest: 14, 13, 12, 11 (closest to 15, excluding 15 itself)
        assert len(records) == 4
        repoints_found = sorted([r.repointing for r in records])
        assert repoints_found == [11, 12, 13, 14]

    def test_get_n_nearest_files_repoint_with_gaps(
        self, hi_l1b_de_files_with_gaps, session
    ):
        """Test repoint case with gaps - finding nearest to repoint 14."""
        dep = {"data_source": "hi", "data_type": "l1b", "descriptor": "45sensor-de"}

        records = get_n_nearest_files_by_repoint(
            session,
            dependency=dep,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
            num_nearest=4,
            repoint=14,
        )

        # Nearest to 14: 15 (dist 1), 13 (dist 1), 12 (dist 2), 11 (dist 3)
        # Ties broken by lower repoint first: 13, 15, 12, 11
        assert len(records) == 4
        repoints_found = sorted([r.repointing for r in records])
        assert repoints_found == [11, 12, 13, 15]

    def test_get_n_nearest_files_repoint_target_missing(
        self, hi_l1b_de_files_with_gaps, session
    ):
        """Test that empty list is returned when target repoint doesn't exist."""
        dep = {"data_source": "hi", "data_type": "l1b", "descriptor": "45sensor-de"}

        records = get_n_nearest_files_by_repoint(
            session,
            dependency=dep,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
            num_nearest=4,
            repoint=17,  # Repoint 17 doesn't exist (gap between 15 and 20)
        )

        assert records == []

    def test_get_n_nearest_files_date_basic(self, swe_l1a_files_with_gaps, session):
        """Test basic date case - finding 3 nearest to Jan 3."""
        from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency import (
            get_n_nearest_files_by_date,
        )

        dep = {"data_source": "swe", "data_type": "l1a", "descriptor": "sci"}

        records = get_n_nearest_files_by_date(
            session,
            dependency=dep,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
            num_nearest=3,
            target_date=datetime(2024, 1, 3),
        )

        # Nearest to Jan 3: Jan 2 (dist 1), Jan 1 (dist 2), Jan 5 (dist 2)
        # Ties broken by earlier date
        assert len(records) == 3
        dates_found = sorted([r.start_date.day for r in records])
        assert dates_found == [1, 2, 5]

    def test_get_n_nearest_files_date_with_gaps(self, swe_l1a_files_with_gaps, session):
        """Test date case with gaps - finding nearest to Jan 5."""
        dep = {"data_source": "swe", "data_type": "l1a", "descriptor": "sci"}

        records = get_n_nearest_files_by_date(
            session,
            dependency=dep,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
            num_nearest=3,
            target_date=datetime(2024, 1, 5),
        )

        # Nearest to Jan 5: Jan 6 (dist 1), Jan 3 (dist 2), Jan 7 (dist 2)
        # Ties broken by earlier date: Jan 3 before Jan 7
        assert len(records) == 3
        dates_found = sorted([r.start_date.day for r in records])
        assert dates_found == [3, 6, 7]

    def test_get_n_nearest_files_date_target_missing(
        self, swe_l1a_files_with_gaps, session
    ):
        """Test that empty list is returned when target date doesn't exist."""
        dep = {"data_source": "swe", "data_type": "l1a", "descriptor": "sci"}

        records = get_n_nearest_files_by_date(
            session,
            dependency=dep,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
            num_nearest=3,
            target_date=datetime(2024, 1, 4),  # Jan 4 doesn't exist (gap)
        )

        assert records == []

    def test_get_n_nearest_files_insufficient(self, hi_l1b_de_files_with_gaps, session):
        """Test when fewer than N files exist - returns all available."""
        dep = {"data_source": "hi", "data_type": "l1b", "descriptor": "45sensor-de"}

        # Ask for 100 nearest, but only 8 others exist (excluding target)
        records = get_n_nearest_files_by_repoint(
            session,
            dependency=dep,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
            num_nearest=100,
            repoint=15,
        )

        # Should return all 8 (10, 11, 12, 13, 14, 20, 21, 22)
        assert len(records) == 8
        repoints_found = sorted([r.repointing for r in records])
        assert repoints_found == [10, 11, 12, 13, 14, 20, 21, 22]


#####################################
# INPROGRESS HELPER FUNCTION TESTS
#####################################


class TestGetInprogressHelpers:
    """Test coverage for _get_inprogress_repoints and _get_inprogress_dates."""

    def test_get_inprogress_repoints_returns_inprogress_only(self, session):
        """Test that only INPROGRESS job repoints are returned."""
        from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency import (
            _get_inprogress_repoints,
        )

        # Create jobs with different statuses
        jobs = [
            ProcessingJob(
                status=models.Status.INPROGRESS,
                instrument="hi",
                data_level="l1b",
                descriptor="45sensor-de",
                start_date=datetime(2024, 1, 1),
                version="v001",
                repointing=10,
            ),
            ProcessingJob(
                status=models.Status.INPROGRESS,
                instrument="hi",
                data_level="l1b",
                descriptor="45sensor-de",
                start_date=datetime(2024, 1, 2),
                version="v001",
                repointing=12,
            ),
            ProcessingJob(
                status=models.Status.SUCCEEDED,
                instrument="hi",
                data_level="l1b",
                descriptor="45sensor-de",
                start_date=datetime(2024, 1, 3),
                version="v001",
                repointing=15,
            ),
            ProcessingJob(
                status=models.Status.FAILED,
                instrument="hi",
                data_level="l1b",
                descriptor="45sensor-de",
                start_date=datetime(2024, 1, 4),
                version="v001",
                repointing=20,
            ),
        ]
        session.add_all(jobs)
        session.commit()

        dep = {"data_source": "hi", "data_type": "l1b", "descriptor": "45sensor-de"}
        result = _get_inprogress_repoints(session, dep)

        # Only INPROGRESS repoints should be returned
        assert result == [10, 12]

    def test_get_inprogress_repoints_filters_by_dependency(self, session):
        """Test that repoints are filtered by instrument/level/descriptor."""
        # Create INPROGRESS jobs for different dependencies
        jobs = [
            ProcessingJob(
                status=models.Status.INPROGRESS,
                instrument="hi",
                data_level="l1b",
                descriptor="45sensor-de",
                start_date=datetime(2024, 1, 1),
                version="v001",
                repointing=10,
            ),
            ProcessingJob(
                status=models.Status.INPROGRESS,
                instrument="hi",
                data_level="l1a",  # Different level
                descriptor="45sensor-de",
                start_date=datetime(2024, 1, 1),
                version="v001",
                repointing=20,
            ),
            ProcessingJob(
                status=models.Status.INPROGRESS,
                instrument="lo",  # Different instrument
                data_level="l1b",
                descriptor="de",
                start_date=datetime(2024, 1, 1),
                version="v001",
                repointing=30,
            ),
        ]
        session.add_all(jobs)
        session.commit()

        dep = {"data_source": "hi", "data_type": "l1b", "descriptor": "45sensor-de"}
        result = _get_inprogress_repoints(session, dep)

        assert result == [10]

    def test_get_inprogress_repoints_empty_when_none(self, session):
        """Test that empty list is returned when no INPROGRESS jobs exist."""
        dep = {"data_source": "hi", "data_type": "l1b", "descriptor": "45sensor-de"}
        result = _get_inprogress_repoints(session, dep)

        assert result == []

    def test_get_inprogress_dates_returns_inprogress_only(self, session):
        """Test that only INPROGRESS job dates are returned."""
        # Create jobs with different statuses
        jobs = [
            ProcessingJob(
                status=models.Status.INPROGRESS,
                instrument="swe",
                data_level="l1a",
                descriptor="sci",
                start_date=datetime(2024, 1, 5),
                version="v001",
            ),
            ProcessingJob(
                status=models.Status.INPROGRESS,
                instrument="swe",
                data_level="l1a",
                descriptor="sci",
                start_date=datetime(2024, 1, 10),
                version="v001",
            ),
            ProcessingJob(
                status=models.Status.SUCCEEDED,
                instrument="swe",
                data_level="l1a",
                descriptor="sci",
                start_date=datetime(2024, 1, 15),
                version="v001",
            ),
            ProcessingJob(
                status=models.Status.FAILED,
                instrument="swe",
                data_level="l1a",
                descriptor="sci",
                start_date=datetime(2024, 1, 20),
                version="v001",
            ),
        ]
        session.add_all(jobs)
        session.commit()

        dep = {"data_source": "swe", "data_type": "l1a", "descriptor": "sci"}
        result = _get_inprogress_dates(session, dep)

        # Only INPROGRESS dates should be returned
        assert result == [datetime(2024, 1, 5), datetime(2024, 1, 10)]

    def test_get_inprogress_dates_filters_by_dependency(self, session):
        """Test that dates are filtered by instrument/level/descriptor."""
        # Create INPROGRESS jobs for different dependencies
        jobs = [
            ProcessingJob(
                status=models.Status.INPROGRESS,
                instrument="swe",
                data_level="l1a",
                descriptor="sci",
                start_date=datetime(2024, 1, 5),
                version="v001",
            ),
            ProcessingJob(
                status=models.Status.INPROGRESS,
                instrument="swe",
                data_level="l1b",  # Different level
                descriptor="sci",
                start_date=datetime(2024, 1, 10),
                version="v001",
            ),
            ProcessingJob(
                status=models.Status.INPROGRESS,
                instrument="idex",  # Different instrument
                data_level="l1a",
                descriptor="sci",
                start_date=datetime(2024, 1, 15),
                version="v001",
            ),
        ]
        session.add_all(jobs)
        session.commit()

        dep = {"data_source": "swe", "data_type": "l1a", "descriptor": "sci"}
        result = _get_inprogress_dates(session, dep)

        assert result == [datetime(2024, 1, 5)]

    def test_get_inprogress_dates_empty_when_none(self, session):
        """Test that empty list is returned when no INPROGRESS jobs exist."""
        dep = {"data_source": "swe", "data_type": "l1a", "descriptor": "sci"}
        result = _get_inprogress_dates(session, dep)

        assert result == []


#####################################
# HI GOODTIMES HELPER FUNCTION TESTS
#####################################


@pytest.fixture
def pointing_table_entries(session):
    """Create pointing table entries for repoints 1-10."""
    from sds_data_manager.lambda_code.SDSCode.database.models import PointingTable

    records = []
    for i in range(1, 11):
        records.append(
            PointingTable(
                pointing_id=i,
                pointing_start_utc=datetime(2024, 1, i, 0, 0, 0),
                pointing_end_utc=datetime(2024, 1, i, 23, 59, 59),
            )
        )
    session.add_all(records)
    session.commit()
    return records


class TestHiGoodtimesHelpers:
    """Test coverage for Hi Goodtimes helper functions."""

    def test_check_pointing_exists_true(self, pointing_table_entries, session):
        """Test _check_pointing_exists returns True when pointing exists."""
        from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency import (
            _check_pointing_exists,
        )

        assert _check_pointing_exists(session, 5) is True
        assert _check_pointing_exists(session, 1) is True
        assert _check_pointing_exists(session, 10) is True

    def test_check_pointing_exists_false(self, pointing_table_entries, session):
        """Test _check_pointing_exists returns False when pointing doesn't exist."""
        from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency import (
            _check_pointing_exists,
        )

        assert _check_pointing_exists(session, 11) is False
        assert _check_pointing_exists(session, 100) is False
        assert _check_pointing_exists(session, 0) is False

    def test_get_hi_goodtimes_target_repoints_basic(self, monkeypatch):
        """Test get_hi_goodtimes_target_repoints for correct [T-N+1, T+N-1]."""
        from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import dependency
        from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency import (
            get_hi_goodtimes_target_repoints,
        )

        # Use smaller number for testing
        monkeypatch.setattr(dependency, "HI_GOODTIMES_NUM_NEAREST_REPOINTS", 2)

        # Trigger repoint 5 with N=2 should return [5-2+1, 5+2-1] = [4, 6]
        targets = get_hi_goodtimes_target_repoints(trigger_repoint=5)

        assert targets == [4, 5, 6]

    def test_get_hi_goodtimes_target_repoints_low_repoint(self, monkeypatch):
        """Test that repoints don't go below 1."""
        from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import dependency
        from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency import (
            get_hi_goodtimes_target_repoints,
        )

        monkeypatch.setattr(dependency, "HI_GOODTIMES_NUM_NEAREST_REPOINTS", 3)

        # Trigger repoint 2 with N=3 should return [max(1, 2-3+1), 2+3-1] = [1, 4]
        targets = get_hi_goodtimes_target_repoints(trigger_repoint=2)

        assert targets == [1, 2, 3, 4]
        assert all(t >= 1 for t in targets)

    def test_get_hi_goodtimes_target_repoints_includes_trigger(self, monkeypatch):
        """Test that the trigger repoint is included in targets."""
        from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import dependency
        from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency import (
            get_hi_goodtimes_target_repoints,
        )

        monkeypatch.setattr(dependency, "HI_GOODTIMES_NUM_NEAREST_REPOINTS", 2)

        targets = get_hi_goodtimes_target_repoints(trigger_repoint=10)

        assert 10 in targets
        assert targets == [9, 10, 11]
