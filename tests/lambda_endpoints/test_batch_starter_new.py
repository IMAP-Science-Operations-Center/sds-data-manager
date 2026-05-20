"""Tests for the batch starter new handler."""

import json
from datetime import datetime

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.database.models import (
    ProcessingJob,
    ScienceFiles,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.batch_starter_new import (  # noqa: E501
    dependency_hash,
    determine_job_version,
    lambda_handler,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.types import (  # noqa: E501
    ProcessingJobNode,
    TimeRange,
)

# ---------------------------------------------------------------------------
# dependency_hash
# ---------------------------------------------------------------------------


def test_dependency_hash_differs_for_different_inputs():
    """dependency_hash yields distinct hashes for different dependency sets."""
    baseline = json.dumps({"files": ["imap_swe_l0_raw_20240101_v001.pkts"]})
    version_bumped = json.dumps({"files": ["imap_swe_l0_raw_20240101_v002.pkts"]})
    different_file = json.dumps({"files": ["imap_swe_l0_sci_20240101_v001.pkts"]})
    same_file = json.dumps({"files": ["imap_swe_l0_raw_20240101_v001.pkts"]})

    hashes = [
        dependency_hash(d)
        for d in (baseline, version_bumped, different_file, same_file)
    ]

    assert all(hashes), "every hash must be non-empty"
    assert len(set(hashes)) == 3, "different inputs produce different hashes"
    assert hashes[0] == hashes[3], "identical inputs produce the same hash"


def test_dependency_hash_is_eight_characters():
    """dependency_hash returns an 8-character hex string."""
    result = dependency_hash(
        json.dumps({"files": ["imap_swe_l0_raw_20240101_v001.pkts"]})
    )
    assert len(result) == 8


# ---------------------------------------------------------------------------
# determine_job_version
# ---------------------------------------------------------------------------


def test_determine_job_version_no_existing_jobs(session):
    """Returns 'v001' when no prior jobs exist."""
    node = ProcessingJobNode(
        source="swe",
        data_type="l1a",
        descriptor="sci",
        time_span=TimeRange.from_string("20240101", "20240101"),
    )
    assert determine_job_version(session, node) == "v001"


def test_determine_job_version_descriptor_is_all(session):
    """Returns 'v001' when descriptor is 'all' and no processing jobs exist."""
    node = ProcessingJobNode(
        source="mag",
        data_type="l1b",
        descriptor="all",
        time_span=TimeRange.from_string("20240101", "20240131"),
    )
    assert determine_job_version(session, node) == "v001"


def test_determine_job_version_with_inprogress_job(session):
    """Returns 'v002' when a v001 job is INPROGRESS."""
    node = ProcessingJobNode(
        source="lo",
        data_type="l1b",
        descriptor="de",
        time_span=TimeRange.from_string("20100101", "20100101"),
    )
    session.add(
        ProcessingJob(
            status=models.Status.INPROGRESS,
            instrument="lo",
            data_level="l1b",
            descriptor="de",
            start_date=datetime(2010, 1, 1),
            version="v001",
            dependency_hash="abc123def456",
        )
    )
    session.commit()

    assert determine_job_version(session, node) == "v002"


def test_determine_job_version_uses_science_file_version(session):
    """Uses the science files table version for standard descriptors.

    Given a SUCCEEDED job at v003 but science file at v001, the next
    version should be v002.
    """
    node = ProcessingJobNode(
        source="lo",
        data_type="l1a",
        descriptor="de",
        time_span=TimeRange.from_string("20240101", "20240101"),
    )
    session.add_all(
        [
            ScienceFiles(
                file_path="/path/to/imap_lo_l1a_de_20240101_v001.cdf",
                instrument="lo",
                data_level="l1a",
                descriptor="de",
                start_date=datetime(2024, 1, 1),
                version="v001",
                extension="cdf",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            ),
            ProcessingJob(
                status=models.Status.SUCCEEDED,
                instrument="lo",
                data_level="l1a",
                descriptor="de",
                start_date=datetime(2024, 1, 1),
                version="v003",
                dependency_hash="123examplehash",
            ),
        ]
    )
    session.commit()

    assert determine_job_version(session, node) == "v002"


def test_determine_job_version_spacecraft_uses_processing_table(session):
    """Uses the ProcessingJob table for spacecraft pointing-attitude jobs."""
    node = ProcessingJobNode(
        source="spacecraft",
        data_type="l1a",
        descriptor="pointing-attitude",
        time_span=TimeRange.from_string("20240101", "20240101"),
    )
    session.add(
        ProcessingJob(
            status=models.Status.SUCCEEDED,
            instrument="spacecraft",
            data_level="l1a",
            descriptor="pointing-attitude",
            start_date=datetime(2024, 1, 1),
            version="v002",
            dependency_hash="123examplehash",
        )
    )
    session.commit()

    assert determine_job_version(session, node) == "v003"


def test_determine_job_version_pointing_days(session):
    """Uses repointing number as the filter when pointing_number_start is set."""
    node = ProcessingJobNode(
        source="glows",
        data_type="l1a",
        descriptor="hist",
        time_span=TimeRange.from_string(
            "20240101", "20240101", pointing_number_start=2
        ),
    )
    session.add_all(
        [
            ProcessingJob(
                status=models.Status.SUCCEEDED,
                instrument="glows",
                data_level="l1a",
                descriptor="hist",
                start_date=datetime(2024, 1, 1),
                version="v004",
                dependency_hash="123examplehash",
                repointing=1,
            ),
            ProcessingJob(
                status=models.Status.SUCCEEDED,
                instrument="glows",
                data_level="l1a",
                descriptor="hist",
                start_date=datetime(2024, 1, 1),
                version="v002",
                dependency_hash="123examplehash",
                repointing=2,
            ),
        ]
    )
    session.commit()

    assert determine_job_version(session, node) == "v003"


# ---------------------------------------------------------------------------
# lambda_handler
# ---------------------------------------------------------------------------


def test_lambda_handler_is_callable():
    """lambda_handler accepts event and context without raising."""
    result = lambda_handler({}, {})
    assert result is None
