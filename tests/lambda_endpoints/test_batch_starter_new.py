"""Minor tests for batch starter handler."""

from datetime import datetime
from unittest.mock import patch

import pytest

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.batch_starter_new import (  # noqa: E501
    IMAPJobHandler,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.utils import (  # noqa: E501
    UpstreamDependencyNode,
)


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


def test_handler_initialization(session, sample_upstream_node):
    """Test that handler initializes and processes job."""
    with patch.object(IMAPJobHandler, "get_dependencies", return_value=None):
        handler = IMAPJobHandler(sample_upstream_node)
        assert handler.dependencies is None
        assert handler.is_duplicate_job is False
        assert handler.job_dependencies_s3_filepath is None


def test_calculate_crid_returns_string(session, sample_upstream_node):
    """Test that _calculate_crid returns a string."""
    handler = IMAPJobHandler(sample_upstream_node)
    result = handler._calculate_crid()
    assert isinstance(result, str)
    assert result == ""


def test_submit_processing_job(session, sample_upstream_node):
    """Test that submit_processing_job returns boolean."""
    handler = IMAPJobHandler(sample_upstream_node)
    result = handler.submit_processing_job()
    assert isinstance(result, bool)
    assert result is True


def test_determine_job_version(session, sample_upstream_node):
    """Test that _determine_job_version is callable without error."""
    handler = IMAPJobHandler(sample_upstream_node)
    # Should not raise an error
    version = handler._determine_job_version()
    assert isinstance(version, str)
    assert version == "v001"
