"""Tests for dependency_new module.

This module provides unit tests for the DependencyConfigReader class used to
read and retrieve upstream dependencies from instrument YAML configuration files.
"""

from unittest.mock import MagicMock, mock_open, patch

import pytest

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.dependency_new import (  # noqa: E501
    DependencyConfigReader,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.types import (  # noqa: E501
    DependencyNode,
)

# Use a short list of instruments that have valid YAML files for testing
TEST_INSTRUMENTS = ["codice", "hi", "lo", "swe"]

MOCK_VALID_INSTRUMENTS = (
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas."
    "dependency_refactoring.dependency_new.VALID_INSTRUMENTS"
)


@pytest.fixture(autouse=True)
def mock_valid_instruments():
    """Automatically mock VALID_INSTRUMENTS for all tests."""
    with patch(
        MOCK_VALID_INSTRUMENTS,
        TEST_INSTRUMENTS,
    ):
        yield


# ---------------------------------------------------------------------------
# DependencyNode validation tests
# ---------------------------------------------------------------------------
def test_validate_node_valid_instrument():
    """Test that valid instrument nodes instantiate without error."""
    node = DependencyNode(source="codice", data_type="l1a", descriptor="all")
    assert node.source == "codice"
    assert node.data_type == "l1a"
    assert node.descriptor == "all"

    node = DependencyNode(
        source="hi",
        data_type="l1b",
        descriptor="hi-counters-aggregated",
        trigger_job=False,
    )
    assert node.trigger_job is False


def test_validate_node_valid_spice():
    """Test that valid SPICE nodes instantiate without error."""
    node = DependencyNode(
        source="leapseconds",
        data_type="spice",
        descriptor="historical",
        trigger_job=False,
    )
    assert node.source == "leapseconds"
    assert node.trigger_job is False


def test_validate_node_with_defaults():
    """Test that nodes with omitted optional fields use correct defaults."""
    node = DependencyNode(
        source="leapseconds", data_type="spice", descriptor="historical"
    )
    assert node.required is True
    assert node.trigger_job is True
    assert node.dependency_query_time_range == []


def test_validate_node_with_date_range():
    """Test that nodes with date range instantiate without error."""
    node = DependencyNode(
        source="hi",
        data_type="l1b",
        descriptor="45sensor-goodtimes",
        dependency_query_time_range=["-3p", "3p"],
    )
    assert node.dependency_query_time_range == ["-3p", "3p"]


def test_validate_node_invalid_source():
    """Test that invalid source raises ValueError."""
    with pytest.raises(ValueError, match="Invalid data source"):
        DependencyNode(source="invalid_source", data_type="l1a", descriptor="all")


def test_validate_node_invalid_data_type():
    """Test that invalid data type raises ValueError."""
    with pytest.raises(ValueError, match="Invalid data type"):
        DependencyNode(source="codice", data_type="invalid_type", descriptor="all")


def test_validate_node_empty_descriptor():
    """Test that empty descriptor raises ValueError."""
    with pytest.raises(ValueError, match="non-empty string"):
        DependencyNode(source="codice", data_type="l1a", descriptor="")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "dependency_query_time_range",
    [
        # Single-element past — one option per cadence type
        ["-3p"],  # pointing
        ["-3h"],  # hourly
        ["-3d"],  # days
        ["-1l"],  # last processed
        # Single-element nearest (positive integer, two-char suffix)
        ["6np"],  # nearest pointing
        ["6nd"],  # nearest day
        # Two-element past + future for each regular cadence option
        ["-3p", "3p"],
        ["-3h", "3h"],
        ["-3d", "3d"],
        # Two-element: past nearest only (future must not be nearest)
        # nearest options are only valid as single-element
    ],
)
def test_validate_date_range_valid(dependency_query_time_range):
    """Test that all valid date range formats are accepted."""
    node = DependencyNode(
        source="hi",
        data_type="l1b",
        descriptor="45sensor-goodtimes",
        dependency_query_time_range=dependency_query_time_range,
    )
    assert node.dependency_query_time_range == dependency_query_time_range


def test_validate_date_range_none():
    """Test that omitting dependency_query_time_range defaults to empty list."""
    node = DependencyNode(
        source="hi",
        data_type="l1b",
        descriptor="45sensor-goodtimes",
    )
    assert node.dependency_query_time_range == []


@pytest.mark.parametrize(
    ("dependency_query_time_range", "match"),
    [
        # Past is positive (should be negative)
        (["3p"], "Invalid past"),
        (["3h"], "Invalid past"),
        (["3d"], "Invalid past"),
        # Past uses unrecognised option letter
        (["-3x"], "Invalid past"),
        # Too many elements
        (["-3p", "3p", "1p"], "1-2 elements"),
        # Empty list — treated as no date range, but an empty list still passes
        # the `not date_range` early-return; test non-list type instead
        ("not-a-list", "1-2 elements"),
        # Future is negative
        (["-3p", "-3p"], "Invalid future"),
        # Future uses nearest option (not allowed)
        (["-3p", "6np"], "Nearest need"),
        (["-3p", "6nd"], "Nearest need"),
        # Future uses unrecognised option letter
        (["-3p", "3x"], "Invalid future"),
    ],
)
def test_validate_date_range_invalid(dependency_query_time_range, match):
    """Test that invalid date range formats raise ValueError."""
    with pytest.raises(ValueError, match=match):
        DependencyNode(
            source="hi",
            data_type="l1b",
            descriptor="45sensor-goodtimes",
            dependency_query_time_range=dependency_query_time_range,
        )


def test_recursive_flatten_list():
    """Test that nested lists are flattened correctly."""
    config = DependencyConfigReader()
    nested_list = [1, [2, 3], [[4], 5]]
    assert config.recursive_flatten_list(nested_list) == [1, 2, 3, 4, 5]

    # Empty list
    assert config.recursive_flatten_list([]) == []
    # Single element list
    assert config.recursive_flatten_list([1]) == [1]
    # Flat list with no nesting
    assert config.recursive_flatten_list([1, 2, 3, 4]) == [1, 2, 3, 4]


def test_load_all_dependencies_all_instruments():
    """Test that we can load all instrument YAML files.

    It tests that we parse them into the expected config format.
    """
    reader = DependencyConfigReader()

    # Verify inputs, outputs, and partition are loaded and not empty
    assert len(reader._config) > 0

    # All keys are accessible via each method
    for key in reader._config:
        assert isinstance(reader.inputs(key), list)
        assert isinstance(reader.outputs(key), list)


@patch(
    MOCK_VALID_INSTRUMENTS,
    ["test-instrument"],
)
def test_load_all_dependencies_missing_file():
    """Test error when YAML file is missing.

    We test by overwriting VALID_INSTRUMENTS to include a non-existent instrument.
    """
    with pytest.raises(FileNotFoundError, match="not found"):
        DependencyConfigReader()


@patch(
    MOCK_VALID_INSTRUMENTS,
    ["codice"],
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas."
    "dependency_refactoring.dependency_new.yaml.safe_load"
)
@patch("builtins.open", new_callable=mock_open)
def test_load_all_dependencies_empty_yaml(mock_file, mock_yaml_load):
    """Test error when YAML content is empty."""
    mock_yaml_load.return_value = None

    with patch(
        "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas."
        "dependency_refactoring.dependency_new.Path"
    ) as mock_path:
        mock_yaml_file = MagicMock()
        mock_yaml_file.exists.return_value = True
        mock_path.return_value.parent.__truediv__.return_value = mock_yaml_file

        with pytest.raises(ValueError, match="empty"):
            DependencyConfigReader()


@patch(
    MOCK_VALID_INSTRUMENTS,
    ["swe"],
)
def test_swe_dependency_config():
    """Test that SWE dependencies are loaded correctly from YAML."""
    reader = DependencyConfigReader()

    # Check that SWE L1A all descriptor has expected dependencies
    l1a_potential_job_node = ("swe", "l1a", "all")
    l1b_potential_job_node = ("swe", "l1b", "sci")
    l2_potential_job_node = ("swe", "l2", "sci")
    l3_potential_job_node = ("swe", "l3", "sci")
    assert l1a_potential_job_node in reader._config
    assert l1b_potential_job_node in reader._config
    assert l2_potential_job_node in reader._config
    assert l3_potential_job_node in reader._config

    # Check that upstream inputs are what we expected for (swe, l1a, all)
    # Inputs: l0/raw, leapseconds/spice, spacecraft_clock/spice
    l1a_inputs = reader.inputs(l1a_potential_job_node)
    assert len(l1a_inputs) == 3

    l0_upstream_dependency = l1a_inputs[0]
    leapseconds_upstream_dependency = l1a_inputs[1]
    spacecraft_clock_upstream_dependency = l1a_inputs[2]
    assert l0_upstream_dependency.source == "swe"
    assert l0_upstream_dependency.data_type == "l0"
    assert l0_upstream_dependency.descriptor == "raw"
    assert l0_upstream_dependency.required is True
    assert l0_upstream_dependency.trigger_job is True
    assert l0_upstream_dependency.dependency_query_time_range == []

    assert leapseconds_upstream_dependency.source == "leapseconds"
    assert leapseconds_upstream_dependency.data_type == "spice"
    assert leapseconds_upstream_dependency.descriptor == "historical"
    assert leapseconds_upstream_dependency.required is True
    assert leapseconds_upstream_dependency.trigger_job is False
    assert leapseconds_upstream_dependency.dependency_query_time_range == []

    assert spacecraft_clock_upstream_dependency.source == "spacecraft_clock"
    assert spacecraft_clock_upstream_dependency.data_type == "spice"
    assert spacecraft_clock_upstream_dependency.descriptor == "historical"
    assert spacecraft_clock_upstream_dependency.required is True
    assert spacecraft_clock_upstream_dependency.trigger_job is False
    assert spacecraft_clock_upstream_dependency.dependency_query_time_range == []

    # Check outputs (required=False)
    l1a_outputs = reader.outputs(l1a_potential_job_node)
    assert len(l1a_outputs) == 2

    swe_l1a_sci_output = l1a_outputs[0]
    swe_l1a_hk_output = l1a_outputs[1]
    assert swe_l1a_sci_output.source == "swe"
    assert swe_l1a_sci_output.data_type == "l1a"
    assert swe_l1a_sci_output.descriptor == "sci"
    assert swe_l1a_sci_output.required is False

    assert swe_l1a_hk_output.source == "swe"
    assert swe_l1a_hk_output.data_type == "l1a"
    assert swe_l1a_hk_output.descriptor == "hk"
    assert swe_l1a_hk_output.required is False