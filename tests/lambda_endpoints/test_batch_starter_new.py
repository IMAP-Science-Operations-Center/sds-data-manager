"""Tests for the batch starter new handler.

Tests are structured to match the business logic from the original batch_starter,
adapted to the IMAPJobHandler API in batch_starter_new.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.database.models import (
    ProcessingJob,
    ScienceFiles,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.batch_starter_new import (  # noqa: E501
    IMAPJobHandler,
    lambda_handler,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.utils import (  # noqa: E501
    UpstreamDependencyNode,
)

_RESOLVER_PATH = (
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas"
    ".dependency_refactoring.batch_starter_new.DependencyResolver"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_upstream_node():
    """Create a sample upstream dependency node for testing."""
    return UpstreamDependencyNode(
        source="swe",
        data_type="l1a",
        descriptor="sci",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 31),
    )


@pytest.fixture
def handler_no_deps(session, sample_upstream_node):
    """Create an IMAPJobHandler whose get_dependencies returns None."""
    with patch.object(IMAPJobHandler, "get_dependencies", return_value=None):
        return IMAPJobHandler(sample_upstream_node)


@pytest.fixture
def handler_with_deps(session, sample_upstream_node):
    """Create an IMAPJobHandler whose get_dependencies returns a dependency set."""
    fake_deps = json.dumps({"files": ["imap_swe_l0_raw_20240101_v001.pkts"]})
    with patch.object(IMAPJobHandler, "get_dependencies", return_value=fake_deps):
        return IMAPJobHandler(sample_upstream_node)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def test_handler_initialization_no_deps(handler_no_deps):
    """Handler initializes correctly and stores dependencies as None."""
    assert hasattr(handler_no_deps, "dependencies")
    assert handler_no_deps.dependencies is None


def test_handler_initialization_with_deps(handler_with_deps):
    """Handler initializes correctly and stores a JSON dependencies string."""
    assert handler_with_deps.dependencies is not None
    assert isinstance(json.loads(handler_with_deps.dependencies), dict)


# ---------------------------------------------------------------------------
# get_dependencies
# ---------------------------------------------------------------------------


def test_get_dependencies_returns_none_when_resolver_fails(
    session, sample_upstream_node
):
    """get_dependencies returns None when resolver reports non-200 status."""
    mock_resolver = MagicMock()
    mock_resolver.get_upstream_dependency.return_value = {
        "status": 404,
        "data": None,
    }
    handler = IMAPJobHandler.__new__(IMAPJobHandler)
    with patch(_RESOLVER_PATH, return_value=mock_resolver):
        result = handler.get_dependencies(sample_upstream_node)
    assert result is None


def test_get_dependencies_returns_json_when_resolver_succeeds(
    session, sample_upstream_node
):
    """get_dependencies returns a JSON string when resolver returns status 200."""
    expected_data = {"files": ["imap_swe_l0_raw_20240101_v001.pkts"]}
    mock_resolver = MagicMock()
    mock_resolver.get_upstream_dependency.return_value = {
        "status": 200,
        "data": expected_data,
    }
    handler = IMAPJobHandler.__new__(IMAPJobHandler)
    with patch(_RESOLVER_PATH, return_value=mock_resolver):
        result = handler.get_dependencies(sample_upstream_node)
    assert result is not None
    assert json.loads(result) == expected_data


# ---------------------------------------------------------------------------
# process_job conditional logic
# ---------------------------------------------------------------------------


def test_process_job_when_no_dependencies(session, sample_upstream_node):
    with patch.object(IMAPJobHandler, "get_dependencies", return_value=None):
        handler = IMAPJobHandler(sample_upstream_node)
        # TODO: Assert that upload dependency file is not called
        assert False


def test_process_job_when_dependencies(session, sample_upstream_node):
    # TODO: Patch out all the calls inside process_jobs
    handler = IMAPJobHandler(sample_upstream_node)
    assert False


# ---------------------------------------------------------------------------
# _determine_job_version
#
# These tests mirror the determine_job_version tests from the original
# batch_starter and describe the intended behavior once the full DB-backed
# implementation is in place.  They set handler.potential_job_node so the
# method can access the instrument/level/descriptor/start_date when it
# queries the database.
# ---------------------------------------------------------------------------


def test_determine_job_version_no_existing_jobs(session, sample_upstream_node):
    """_determine_job_version returns 'v001' when no prior jobs exist."""
    handler = IMAPJobHandler.__new__(IMAPJobHandler)
    handler.potential_job_node = sample_upstream_node
    version = handler._determine_job_version()
    assert version == "v001"


def test_determine_job_version_descriptor_is_all(session):
    """_determine_job_version returns 'v001' when descriptor contains 'all' and
    no processing jobs exist.
    """
    # TODO is this correct behavior?
    node = UpstreamDependencyNode(
        source="mag",
        data_type="l1b",
        descriptor="all",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 31),
    )
    handler = IMAPJobHandler.__new__(IMAPJobHandler)
    handler.potential_job_node = node
    version = handler._determine_job_version()
    assert version == "v001"


def test_determine_job_version_with_inprogress_job(session):
    """_determine_job_version returns 'v002' when a v001 job is INPROGRESS."""
    node = UpstreamDependencyNode(
        source="lo",
        data_type="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        end_date=datetime(2010, 1, 1),
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

    handler = IMAPJobHandler.__new__(IMAPJobHandler)
    handler.potential_job_node = node
    version = handler._determine_job_version()
    assert version == "v002"


def test_determine_job_version_uses_science_file_version(session):
    """_determine_job_version uses the science files table version, not the
    processing job version, for non-'all' and non-spacecraft-pointing descriptors.

    Mirrors test_determine_job_version_science from the original test file:
    given a SUCCEEDED job at v003 but science file at v001, the next version
    should be v002.
    """
    node = UpstreamDependencyNode(
        source="lo",
        data_type="l1a",
        descriptor="de",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 1),
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

    handler = IMAPJobHandler.__new__(IMAPJobHandler)
    handler.potential_job_node = node
    version = handler._determine_job_version()
    assert version == "v002"


def test_determine_job_version_spacecraft_uses_processing_table(session):
    """_determine_job_version uses the ProcessingJob table for spacecraft
    pointing-attitude jobs.

    Mirrors test_determine_job_version_spacecraft from the original test file.
    """
    node = UpstreamDependencyNode(
        source="spacecraft",
        data_type="l1a",
        descriptor="pointing-attitude",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 1),
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

    handler = IMAPJobHandler.__new__(IMAPJobHandler)
    handler.potential_job_node = node
    version = handler._determine_job_version()
    assert version == "v003"


def test_determine_job_version_pointing_days(session):
    node = UpstreamDependencyNode(
        source="glows",
        data_type="l1a",
        descriptor="hist",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 1),
        repoint=2,
    )
    session.add(
        ProcessingJob(
            status=models.Status.SUCCEEDED,
            instrument="glows",
            data_level="l1a",
            descriptor="hist",
            start_date=datetime(2024, 1, 1),
            version="v004",
            dependency_hash="123examplehash",
            repointing=1,
        )
    )
    session.add(
        ProcessingJob(
            status=models.Status.SUCCEEDED,
            instrument="glows",
            data_level="l1a",
            descriptor="hist",
            start_date=datetime(2024, 1, 1),
            version="v002",
            dependency_hash="123examplehash",
            repointing=2,
        )
    )
    session.commit()

    # version should use repointing in addition to the start_date
    handler = IMAPJobHandler.__new__(IMAPJobHandler)
    handler.potential_job_node = node
    version = handler._determine_job_version()
    assert version == "v003"


# ---------------------------------------------------------------------------
# _calculate_crid
#
# Mirrors the CRID role from the original batch_starter: a hash uniquely
# identifying a job's upstream inputs so different input files / different
# input versions are distinguishable, and the resulting CRID is persisted on
# the ProcessingJob row produced by process_job.
# ---------------------------------------------------------------------------


def test_calculate_crid_differs_for_inputs_and_versions(session, sample_upstream_node):
    """_calculate_crid yields a distinct CRID for each distinct dependency set.

    Covers both axes in one test: a version bump on the same file and a swap
    to a different file must each produce a CRID different from the baseline.
    """
    baseline = json.dumps({"files": ["imap_swe_l0_raw_20240101_v001.pkts"]})
    version_bumped = json.dumps({"files": ["imap_swe_l0_raw_20240101_v002.pkts"]})
    different_file = json.dumps({"files": ["imap_swe_l0_sci_20240101_v001.pkts"]})
    same_file = json.dumps({"files": ["imap_swe_l0_raw_20240101_v001.pkts"]})

    crids = []
    for deps in (baseline, version_bumped, different_file, same_file):
        with patch.object(IMAPJobHandler, "get_dependencies", return_value=deps):
            handler = IMAPJobHandler(sample_upstream_node)
            crids.append(handler._calculate_crid())

    assert all(crids), "every CRID must be non-empty"
    assert len(set(crids)) == 3, "different inputs/versions must yield different CRIDs"
    assert crids[0] == crids[3]


def test_crid_persisted_to_processing_job(session, sample_upstream_node):
    """process_job stores the calculated CRID on the ProcessingJob row.

    Mirrors how the original batch_starter writes its dependency hash onto the
    ProcessingJob entry it inserts before submitting the batch job.
    """
    deps = json.dumps({"files": ["imap_swe_l0_raw_20240101_v001.pkts"]})
    with patch.object(IMAPJobHandler, "get_dependencies", return_value=deps):
        handler = IMAPJobHandler(sample_upstream_node)

    expected_crid = handler._calculate_crid()
    job = (
        session.query(ProcessingJob)
        .filter(
            ProcessingJob.instrument == sample_upstream_node.source,
            ProcessingJob.data_level == sample_upstream_node.data_type,
            ProcessingJob.descriptor == sample_upstream_node.descriptor,
        )
        .first()
    )
    assert job is not None, "process_job should insert a ProcessingJob row"
    assert job.dependency_hash == expected_crid


# ---------------------------------------------------------------------------
# is_duplicate_job
#
# These tests describe the intended duplicate-detection behavior, mirroring
# the duplicate-job logic verified in the original batch_starter tests.
# ---------------------------------------------------------------------------


def test_is_duplicate_job_returns_false_when_no_prior_jobs(
    session, sample_upstream_node
):
    """is_duplicate_job returns False when no matching job exists in the DB."""
    handler = IMAPJobHandler.__new__(IMAPJobHandler)
    assert handler.is_duplicate_job(sample_upstream_node, "abc123") is False


def test_is_duplicate_job_returns_false_for_failed_job(session):
    """is_duplicate_job returns False when the only matching job has status FAILED."""
    node = UpstreamDependencyNode(
        source="lo",
        data_type="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        end_date=datetime(2010, 1, 1),
    )
    session.add(
        ProcessingJob(
            status=models.Status.FAILED,
            instrument="lo",
            data_level="l1b",
            descriptor="de",
            start_date=datetime(2010, 1, 1),
            version="v001",
            dependency_hash="27005a05",
        )
    )
    session.commit()

    handler = IMAPJobHandler.__new__(IMAPJobHandler)
    assert handler.is_duplicate_job(node, "27005a05") is False


def test_is_duplicate_job_returns_true_for_inprogress_job(session):
    """is_duplicate_job returns True when a matching INPROGRESS job exists."""
    node = UpstreamDependencyNode(
        source="lo",
        data_type="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        end_date=datetime(2010, 1, 1),
    )
    session.add(
        ProcessingJob(
            status=models.Status.INPROGRESS,
            instrument="lo",
            data_level="l1b",
            descriptor="de",
            start_date=datetime(2010, 1, 1),
            version="v001",
            dependency_hash="27005a05",
        )
    )
    session.commit()

    handler = IMAPJobHandler.__new__(IMAPJobHandler)
    assert handler.is_duplicate_job(node, "27005a05") is True


def test_is_duplicate_job_returns_true_for_succeeded_job(session):
    """is_duplicate_job returns True when a matching SUCCEEDED job exists."""
    node = UpstreamDependencyNode(
        source="lo",
        data_type="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        end_date=datetime(2010, 1, 1),
    )
    session.add(
        ProcessingJob(
            status=models.Status.SUCCEEDED,
            instrument="lo",
            data_level="l1b",
            descriptor="de",
            start_date=datetime(2010, 1, 1),
            version="v001",
            dependency_hash="27005a05",
        )
    )
    session.commit()

    handler = IMAPJobHandler.__new__(IMAPJobHandler)
    assert handler.is_duplicate_job(node, "27005a05") is True


def test_is_duplicate_job_returns_false_for_different_hash(session):
    """is_duplicate_job returns False when dependency hash does not match."""
    node = UpstreamDependencyNode(
        source="lo",
        data_type="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        end_date=datetime(2010, 1, 1),
    )
    session.add(
        ProcessingJob(
            status=models.Status.INPROGRESS,
            instrument="lo",
            data_level="l1b",
            descriptor="de",
            start_date=datetime(2010, 1, 1),
            version="v001",
            dependency_hash="27005a05",
        )
    )
    session.commit()

    handler = IMAPJobHandler.__new__(IMAPJobHandler)
    assert handler.is_duplicate_job(node, "different-hash") is False


def test_clean_up_runs_without_error(handler_no_deps):
    """clean_up completes without raising an exception."""
    handler_no_deps.clean_up()


def test_lambda_handler_is_callable():
    """lambda_handler accepts event and context without raising."""
    result = lambda_handler({}, {})
    assert result is None
