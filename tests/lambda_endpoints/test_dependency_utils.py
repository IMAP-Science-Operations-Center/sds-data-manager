"""Tests for dependency_utils module.

This module provides unit tests for the DependencyConfigNew class used to
read and retrieve upstream dependencies from instrument YAML configuration files.
"""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_utils import (
    DependencyConfigNew,
)

# Use a short list of instruments that have valid YAML files for testing
TEST_INSTRUMENTS = ["codice", "hi", "lo", "swe"]

@pytest.fixture(autouse=True)
def mock_valid_instruments():
    """Automatically mock VALID_INSTRUMENTS for all tests."""
    with patch(
        "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_utils.VALID_INSTRUMENTS",
        TEST_INSTRUMENTS
    ):
        yield


# Tests for validate_node
def test_validate_node_valid_instrument():
    """Test validation of valid instrument nodes."""
    config = DependencyConfigNew()
    assert config.validate_node(("codice", "l1a", "all")) is True
    assert config.validate_node(("hi", "l1b", "hi-counters-aggregated")) is True


def test_validate_node_valid_spice():
    """Test validation of valid SPICE nodes."""
    config = DependencyConfigNew()
    assert config.validate_node(("leapseconds", "spice", "historical")) is True


def test_validate_node_not_tuple():
    """Test that non-tuple raises ValueError."""
    config = DependencyConfigNew()
    with pytest.raises(ValueError, match="must be a 3-element tuple"):
        config.validate_node(["codice", "l1a", "all"])


def test_validate_node_wrong_length():
    """Test that wrong tuple length raises ValueError."""
    config = DependencyConfigNew()
    with pytest.raises(ValueError, match="3-element tuple"):
        config.validate_node(("codice", "l1a"))


def test_validate_node_invalid_source():
    """Test that invalid source raises ValueError."""
    config = DependencyConfigNew()
    with pytest.raises(ValueError, match="Invalid data source"):
        config.validate_node(("invalid_source", "l1a", "all"))


def test_validate_node_invalid_data_type():
    """Test that invalid data type raises ValueError."""
    config = DependencyConfigNew()
    with pytest.raises(ValueError, match="Invalid data type"):
        config.validate_node(("codice", "invalid_type", "all"))


def test_validate_node_empty_descriptor():
    """Test that empty descriptor raises ValueError."""
    config = DependencyConfigNew()
    with pytest.raises(ValueError, match="non-empty string"):
        config.validate_node(("codice", "l1a", ""))


# Tests for load_all_dependencies (now part of __init__)
@patch("sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_utils.VALID_INSTRUMENTS", ["codice"])
@patch("sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_utils.yaml.safe_load")
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

    with patch("sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_utils.Path") as mock_path:
        mock_yaml_file = MagicMock()
        mock_yaml_file.exists.return_value = True
        mock_path.return_value.parent.__truediv__.return_value = mock_yaml_file

        config = DependencyConfigNew()

        # Should have entries for both YAML keys
        assert len(config.config) == 2
        assert ("codice", "l1a", "all") in config.config
        assert ("codice", "l1b", "hi-counters-aggregated") in config.config


@patch("sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_utils.VALID_INSTRUMENTS", ["test-instrument"])
def test_load_all_dependencies_missing_file():
    """Test error when YAML file is missing.
    
    We test by overwriting VALID_INSTRUMENTS to include a non-existent instrument."""
    with pytest.raises(FileNotFoundError, match="not found"):
        DependencyConfigNew()


@patch("sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_utils.VALID_INSTRUMENTS", ["codice"])
@patch("sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_utils.yaml.safe_load")
@patch("builtins.open", new_callable=mock_open)
def test_load_all_dependencies_empty_yaml(mock_file, mock_yaml_load):
    """Test error when YAML content is empty."""
    mock_yaml_load.return_value = None

    with patch("sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_utils.Path") as mock_path:
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
    config = DependencyConfigNew()
    dependency_config = {
        ("swe", "l1b", "swe-all"): [("swe", "l1a", "all")],
        ("swe", "l2", "swe-sci-1mo"): [("swe", "l1b", "swe-all")],
        ("mag", "l1b", "mag-all"): [("mag", "l1a", "all")],
    }
    config._config = dependency_config

    result = config.get_downstream_dependency_nodes(("swe", "l1a", "all"))

    assert len(result) == 1
    assert ("swe", "l1b", "swe-all") in result


def test_get_downstream_dependency_nodes_multiple():
    """Test getting multiple downstream dependencies for a science file ingestion."""
    config = DependencyConfigNew()
    dependency_config = {
        ("codice", "l1b", "lo-counters"): [("codice", "l1a", "all")],
        ("codice", "l1b", "hi-counters"): [("codice", "l1a", "all")],
        ("codice", "l2", "codice-sci"): [("codice", "l1a", "all")],
        ("mag", "l1b", "mag-all"): [("mag", "l1a", "all")],
    }
    config._config = dependency_config

    result = config.get_downstream_dependency_nodes(("codice", "l1a", "all"))

    assert len(result) == 3
    assert ("codice", "l1b", "lo-counters") in result
    assert ("codice", "l1b", "hi-counters") in result
    assert ("codice", "l2", "codice-sci") in result


def test_get_downstream_dependency_nodes_no_dependents():
    """Test when a node has no downstream dependencies."""
    config = DependencyConfigNew()
    dependency_config = {
        ("swe", "l1b", "swe-all"): [("swe", "l1a", "all")],
        ("mag", "l1b", "mag-all"): [("mag", "l1a", "all")],
    }
    config._config = dependency_config

    result = config.get_downstream_dependency_nodes(("codice", "l2", "all"))

    assert len(result) == 0


def test_get_downstream_dependency_nodes_shared_dependency():
    """Test when multiple nodes depend on a shared upstream node such as SPICE data."""
    config = DependencyConfigNew()
    dependency_config = {
        ("swe", "l1b", "swe-sci"): [("leapseconds", "spice", "historical"), ("swe", "l1a", "all")],
        ("mag", "l1b", "mag-sci"): [("leapseconds", "spice", "historical"), ("mag", "l1a", "all")],
        ("hi", "l1b", "hi-sci"): [("leapseconds", "spice", "historical"), ("hi", "l1a", "all")],
    }
    config._config = dependency_config

    result = config.get_downstream_dependency_nodes(("leapseconds", "spice", "historical"))

    assert len(result) == 3
    assert ("swe", "l1b", "swe-sci") in result
    assert ("mag", "l1b", "mag-sci") in result
    assert ("hi", "l1b", "hi-sci") in result


def test_get_downstream_dependency_nodes_ancillary_files():
    """Test ancillary files as upstream dependency that kickoff processing jobs.
    
    Realistic scenario where a single ancillary file from an instrument can trigger
    one or multiple downstream jobs at different processing levels. For example,
    a calibration LUT ingested can trigger l1a processing, which then enables l1b
    and potentially l2 processing downstream.
    """
    config = DependencyConfigNew()
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
    config._config = dependency_config

    # Single ancillary file kicking off single l1a job
    codice_lut_jobs = config.get_downstream_dependency_nodes(
        ("codice", "ancillary", "l1a-lut")
    )
    assert len(codice_lut_jobs) == 1
    assert ("codice", "l1a", "all") in codice_lut_jobs

    # Single ancillary calibration file triggering multiple l1b jobs
    # (different descriptor variants of same level)
    codice_cal_jobs = config.get_downstream_dependency_nodes(
        ("codice", "ancillary", "l1b-calibration")
    )
    assert len(codice_cal_jobs) == 3
    assert ("codice", "l1b", "lo-counters") in codice_cal_jobs
    assert ("codice", "l1b", "hi-counters") in codice_cal_jobs
    assert ("codice", "l1b", "hk") in codice_cal_jobs

    # Single ancillary background model triggering multiple l2 cadence jobs
    bg_model_jobs = config.get_downstream_dependency_nodes(
        ("ultra", "ancillary", "l2-background")
    )
    assert len(bg_model_jobs) == 2
    assert ("ultra", "l2", "ultra-sci-1mo") in bg_model_jobs
    assert ("ultra", "l2", "ultra-sci-3mo") in bg_model_jobs
    # Verify different instrument ancillary also works
    hi_cal_jobs = config.get_downstream_dependency_nodes(
        ("hi", "ancillary", "l1b-calibration")
    )
    assert len(hi_cal_jobs) == 1
    assert ("hi", "l1b", "hi-all") in hi_cal_jobs


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
    config = DependencyConfigNew()
    dependency_config = {
        # Downstream product with mixed upstream dependency types
        ("hi", "l1b", "45sensor-goodtimes"): [
            # Dependency with pointing-based time range: 3 pointings past to 3 pointings future
            ("hi", "l1b", "45sensor-de", True, True, ("3p", "3p")),
            # Dependency without time range
            ("hi", "l1b", "45sensor-hk", True, True),
            # Another dependency without time range
            ("hi", "l1a", "45sensor-diagfee", True, True),
            # SPICE dependency (typically no time range)
            ("leapseconds", "spice", "historical", True, False),
        ],
    }
    config._config = dependency_config

    # Test downstream lookup for goodtimes product
    goodtimes_upstream = dependency_config[("hi", "l1b", "45sensor-goodtimes")]
    
    # Verify it has 4 upstream dependencies
    assert len(goodtimes_upstream) == 4
    
    # Verify mixed tuple lengths and formats are present
    assert ("hi", "l1b", "45sensor-de", True, True, ("3p", "3p")) in goodtimes_upstream
    assert ("hi", "l1b", "45sensor-hk", True, True) in goodtimes_upstream
    assert ("hi", "l1a", "45sensor-diagfee", True, True) in goodtimes_upstream
    assert ("leapseconds", "spice", "historical", True, False) in goodtimes_upstream
