"""Tests for dependency_new module.

This module provides unit tests for the DependencyConfigNew class used to
read and retrieve upstream dependencies from instrument YAML configuration files.
"""

from unittest.mock import MagicMock, mock_open, patch

import pytest

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_new import (
    DependencyConfigNew,
    DependencyResolver,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.utils import DependencyNode

# Use a short list of instruments that have valid YAML files for testing
TEST_INSTRUMENTS = ["codice", "hi", "lo", "swe"]


@pytest.fixture(autouse=True)
def mock_valid_instruments():
    """Automatically mock VALID_INSTRUMENTS for all tests."""
    with patch(
        "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_new.VALID_INSTRUMENTS",
        TEST_INSTRUMENTS,
    ):
        yield


# Tests for validate_node
def test_validate_node_valid_instrument():
    """Test validation of valid instrument nodes."""
    config = DependencyConfigNew()
    assert config.validate_node(("codice", "l1a", "all", True, True)) is True
    assert (
        config.validate_node(("hi", "l1b", "hi-counters-aggregated", True, False))
        is True
    )


def test_validate_node_valid_spice():
    """Test validation of valid SPICE nodes."""
    config = DependencyConfigNew()
    assert (
        config.validate_node(("leapseconds", "spice", "historical", True, False))
        is True
    )


def test_validate_node_not_tuple():
    """Test that non-tuple raises ValueError."""
    config = DependencyConfigNew()
    with pytest.raises(ValueError, match="must be a 5-element tuple"):
        config.validate_node(["codice", "l1a", "all", True, True])


def test_validate_node_wrong_length():
    """Test that wrong tuple length raises ValueError."""
    config = DependencyConfigNew()
    with pytest.raises(ValueError, match="5-element tuple"):
        config.validate_node(("codice", "l1a"))


def test_validate_node_invalid_source():
    """Test that invalid source raises ValueError."""
    config = DependencyConfigNew()
    with pytest.raises(ValueError, match="Invalid data source"):
        config.validate_node(("invalid_source", "l1a", "all", True, True))


def test_validate_node_invalid_data_type():
    """Test that invalid data type raises ValueError."""
    config = DependencyConfigNew()
    with pytest.raises(ValueError, match="Invalid data type"):
        config.validate_node(("codice", "invalid_type", "all", True, True))


def test_validate_node_empty_descriptor():
    """Test that empty descriptor raises ValueError."""
    config = DependencyConfigNew()
    with pytest.raises(ValueError, match="non-empty string"):
        config.validate_node(("codice", "l1a", "", True, True))


# Tests for load_all_dependencies (now part of __init__)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_new.VALID_INSTRUMENTS",
    ["codice"],
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_new.yaml.safe_load"
)
@patch("builtins.open", new_callable=mock_open)
def test_load_all_dependencies_success(mock_file, mock_yaml_load):
    """Test successful loading of all dependencies."""
    mock_config = {
        "(l1a, all)": [
            ("codice", "l0", "raw", True, True),
            ("leapseconds", "spice", "historical", True, False),
        ],
        "(l1b, hi-counters-aggregated)": [
            ("codice", "l1a", "hi-counters-aggregated", True, True),
        ],
    }
    mock_yaml_load.return_value = mock_config

    with patch(
        "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_new.Path"
    ) as mock_path:
        mock_yaml_file = MagicMock()
        mock_yaml_file.exists.return_value = True
        mock_path.return_value.parent.__truediv__.return_value = mock_yaml_file

        config = DependencyConfigNew()

        # Should have entries for both YAML keys
        assert len(config.config) == 2
        assert ("codice", "l1a", "all") in config.config
        assert ("codice", "l1b", "hi-counters-aggregated") in config.config


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_new.VALID_INSTRUMENTS",
    ["test-instrument"],
)
def test_load_all_dependencies_missing_file():
    """Test error when YAML file is missing.

    We test by overwriting VALID_INSTRUMENTS to include a non-existent instrument.
    """
    with pytest.raises(FileNotFoundError, match="not found"):
        DependencyConfigNew()


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_new.VALID_INSTRUMENTS",
    ["codice"],
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_new.yaml.safe_load"
)
@patch("builtins.open", new_callable=mock_open)
def test_load_all_dependencies_empty_yaml(mock_file, mock_yaml_load):
    """Test error when YAML content is empty."""
    mock_yaml_load.return_value = None

    with patch(
        "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_new.Path"
    ) as mock_path:
        mock_yaml_file = MagicMock()
        mock_yaml_file.exists.return_value = True
        mock_path.return_value.parent.__truediv__.return_value = mock_yaml_file

        with pytest.raises(ValueError, match="empty"):
            DependencyConfigNew()


# Tests for get_cadence_job


def test_get_cadence_job_1mo():
    """Test extracting 1mo cadence from descriptor."""
    config = DependencyConfigNew()
    result = config.get_cadence_job("swe-sci-1mo")
    assert result == "1mo"


def test_get_cadence_job_3mo():
    """Test extracting 3mo cadence from descriptor."""
    config = DependencyConfigNew()
    result = config.get_cadence_job("mag-norm-3mo")
    assert result == "3mo"


def test_get_cadence_job_6mo():
    """Test extracting 6mo cadence from descriptor."""
    config = DependencyConfigNew()
    result = config.get_cadence_job("codice-data-6mo")
    assert result == "6mo"


def test_get_cadence_job_1yr():
    """Test extracting 1yr cadence from descriptor."""
    config = DependencyConfigNew()
    result = config.get_cadence_job("hi-survey-1yr")
    assert result == "1yr"


def test_get_cadence_job_no_cadence():
    """Test descriptor with no cadence indicator returns None."""
    config = DependencyConfigNew()
    result = config.get_cadence_job("swe-sci-raw")
    assert result is None


def test_get_cadence_job_empty_descriptor():
    """Test empty descriptor returns None."""
    config = DependencyConfigNew()
    result = config.get_cadence_job("")
    assert result is None


# Tests for get_downstream_dependency_nodes


def test_get_downstream_dependency_nodes_single():
    """Test getting downstream dependencies for science file ingestions."""
    dependency_resolver = DependencyResolver()
    dependency_config = {
        ("swe", "l1b", "swe-all"): [("swe", "l1a", "all", True, True)],
        ("swe", "l2", "swe-sci-1mo"): [("swe", "l1b", "swe-all", True, True)],
        ("mag", "l1b", "mag-all"): [("mag", "l1a", "all", True, True)],
    }
    dependency_resolver._config = dependency_config

    result = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="swe", data_type="l1a", product_name="all")
    )

    assert len(result) == 1
    assert DependencyNode(source="swe", data_type="l1b", product_name="swe-all") in result


def test_get_downstream_dependency_nodes_multiple():
    """Test getting multiple downstream dependencies for a science file ingestion."""
    dependency_resolver = DependencyResolver()
    dependency_config = {
        ("codice", "l1b", "lo-counters"): [("codice", "l1a", "all", True, True)],
        ("codice", "l1b", "hi-counters"): [("codice", "l1a", "all", True, True)],
        ("codice", "l2", "codice-sci"): [("codice", "l1a", "all", True, True)],
        ("mag", "l1b", "mag-all"): [("mag", "l1a", "all", True, True)],
    }
    dependency_resolver._config = dependency_config

    result = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="codice", data_type="l1a", product_name="all")
    )

    assert len(result) == 3
    assert DependencyNode(source="codice", data_type="l1b", product_name="lo-counters") in result
    assert DependencyNode(source="codice", data_type="l1b", product_name="hi-counters") in result
    assert DependencyNode(source="codice", data_type="l2", product_name="codice-sci") in result


def test_get_downstream_dependency_nodes_no_dependents():
    """Test when a node has no downstream dependencies."""
    dependency_resolver = DependencyResolver()
    dependency_config = {
        ("swe", "l1b", "swe-all"): [("swe", "l1a", "all", True, True)],
        ("mag", "l1b", "mag-all"): [("mag", "l1a", "all", True, True)],
    }
    dependency_resolver._config = dependency_config

    result = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="codice", data_type="l2", product_name="all")
    )

    assert len(result) == 0


def test_get_downstream_dependency_nodes_shared_dependency():
    """Test when multiple nodes depend on a shared upstream node such as SPICE data."""
    dependency_resolver = DependencyResolver()
    dependency_config = {
        ("swe", "l1b", "swe-sci"): [
            ("leapseconds", "spice", "historical", True, True),
            ("swe", "l1a", "all", True, True),
        ],
        ("mag", "l1b", "mag-sci"): [
            ("leapseconds", "spice", "historical", True, True),
            ("mag", "l1a", "all", True, True),
        ],
        ("hi", "l1b", "hi-sci"): [
            ("leapseconds", "spice", "historical", True, True),
            ("hi", "l1a", "all", True, True),
        ],
    }
    dependency_resolver._config = dependency_config

    result = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="leapseconds", data_type="spice", product_name="historical")
    )

    assert len(result) == 3
    assert DependencyNode(source="swe", data_type="l1b", product_name="swe-sci") in result
    assert DependencyNode(source="mag", data_type="l1b", product_name="mag-sci") in result
    assert DependencyNode(source="hi", data_type="l1b", product_name="hi-sci") in result


def test_get_downstream_dependency_nodes_ancillary_files():
    """Test ancillary files as upstream dependency that kickoff processing jobs.

    Realistic scenario where a single ancillary file from an instrument can trigger
    one or multiple downstream jobs at different processing levels. For example,
    a calibration LUT ingested can trigger l1a processing, which then enables l1b
    and potentially l2 processing downstream.
    """
    dependency_resolver = DependencyResolver()
    dependency_config = {
        # Single ancillary LUT triggers only one l1a job
        ("codice", "l1a", "all"): [("codice", "ancillary", "l1a-lut", True, True)],
        # Single ancillary calibration triggers multiple l1b descriptor variants
        ("codice", "l1b", "lo-counters"): [
            ("codice", "l1a", "all", True, True),
            ("codice", "ancillary", "l1b-calibration", True, True),
        ],
        ("codice", "l1b", "hi-counters"): [
            ("codice", "l1a", "all", True, True),
            ("codice", "ancillary", "l1b-calibration", True, True),
        ],
        ("codice", "l1b", "hk"): [
            ("codice", "l1a", "all", True, True),
            ("codice", "ancillary", "l1b-calibration", True, True),
        ],
        # Single ancillary background model triggers multiple l2 descriptor variants
        ("ultra", "l2", "ultra-sci-1mo"): [
            ("ultra", "l2", "all", True, True),
            ("ultra", "ancillary", "l2-background", False, True),
        ],
        ("ultra", "l2", "ultra-sci-3mo"): [
            ("ultra", "l2", "all", True, True),
            ("ultra", "ancillary", "l2-background", False, True),
        ],
        # Different instruments with similar pattern
        ("hi", "l1a", "all"): [("hi", "ancillary", "l1a-lut", True, True)],
        ("hi", "l1b", "hi-all"): [
            ("hi", "l1a", "all", True, True),
            ("hi", "ancillary", "l1b-calibration", True, True),
        ],
    }
    dependency_resolver._config = dependency_config

    # Single ancillary file kicking off single l1a job
    codice_lut_jobs = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="codice", data_type="ancillary", product_name="l1a-lut")
    )
    assert len(codice_lut_jobs) == 1
    assert DependencyNode(source="codice", data_type="l1a", product_name="all") in codice_lut_jobs

    # Single ancillary calibration file triggering multiple l1b jobs
    # (different descriptor variants of same level)
    codice_cal_jobs = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="codice", data_type="ancillary", product_name="l1b-calibration")
    )
    assert len(codice_cal_jobs) == 3
    assert DependencyNode(source="codice", data_type="l1b", product_name="lo-counters") in codice_cal_jobs
    assert DependencyNode(source="codice", data_type="l1b", product_name="hi-counters") in codice_cal_jobs
    assert DependencyNode(source="codice", data_type="l1b", product_name="hk") in codice_cal_jobs

    # Single ancillary background model triggering multiple l2 cadence jobs
    bg_model_jobs = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="ultra", data_type="ancillary", product_name="l2-background")
    )
    assert len(bg_model_jobs) == 2
    assert DependencyNode(source="ultra", data_type="l2", product_name="ultra-sci-1mo") in bg_model_jobs
    assert DependencyNode(source="ultra", data_type="l2", product_name="ultra-sci-3mo") in bg_model_jobs
    # Verify different instrument ancillary also works
    hi_cal_jobs = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="hi", data_type="ancillary", product_name="l1b-calibration")
    )
    assert len(hi_cal_jobs) == 1
    assert DependencyNode(source="hi", data_type="l1b", product_name="hi-all") in hi_cal_jobs


def test_get_downstream_dependency_nodes_with_date_ranges():
    """Test downstream lookup with dependencies that include date ranges.

    Complex scenario where upstream dependencies include optional time range fields
    (past_range, future_range) with format specifiers:
    - p: pointing
    - h: hourly
    - d: days
    - l: last_processed

    A single downstream product can depend on multiple upstream products with
    varying tuple lengths and time range formats.
    """
    dependency_resolver = DependencyResolver()
    dependency_config = {
        # Downstream product with mixed upstream dependency types
        ("hi", "l1b", "45sensor-goodtimes"): [
            # Dependency with pointing-based time range:
            # 3 pointings past to 3 pointings future
            ("hi", "l1b", "45sensor-de", True, True, ("-3p", "3p")),
            # Dependency without time range
            ("hi", "l1b", "45sensor-hk", True, True),
            # Another dependency without time range
            ("hi", "l1a", "45sensor-diagfee", True, True),
            # SPICE dependency (typically no time range)
            ("leapseconds", "spice", "historical", True, False),
        ],
        ("hi", "l1b", "45sensor-de"): [
            ("hi", "l1a", "45sensor-de", True, True),
            ("hi", "l1b", "45sensor-goodtimes", True, True),
            ("leapseconds", "spice", "historical", True, False),
        ],
    }
    dependency_resolver._config = dependency_config

    # Test downstream lookup for goodtimes product
    de_downstream = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="hi", data_type="l1b", product_name="45sensor-de")
    )

    # Verify it has 1 downstream dependency
    assert len(de_downstream) == 1
    assert DependencyNode(source="hi", data_type="l1b", product_name="45sensor-goodtimes") in de_downstream