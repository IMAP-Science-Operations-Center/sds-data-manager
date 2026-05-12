"""Tests for dependency_new module.

This module provides unit tests for the DependencyConfigReader class used to
read and retrieve upstream dependencies from instrument YAML configuration files.
"""

from unittest.mock import MagicMock, mock_open, patch

import pytest

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.dependency_new import (  # noqa: E501
    DependencyConfigReader,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.utils import (  # noqa: E501
    format_upstream_node_input,
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


# Tests for node validation via format_upstream_node_input
def test_validate_node_valid_instrument():
    """Test that valid instrument nodes instantiate without error."""
    format_upstream_node_input(
        {
            "upstream_source": "codice",
            "upstream_data_type": "l1a",
            "upstream_descriptor": "all",
            "required": True,
            "kickoff_job": True,
        }
    )
    format_upstream_node_input(
        {
            "upstream_source": "hi",
            "upstream_data_type": "l1b",
            "upstream_descriptor": "hi-counters-aggregated",
            "required": True,
            "kickoff_job": False,
        }
    )


def test_validate_node_valid_spice():
    """Test that valid SPICE nodes instantiate without error."""
    format_upstream_node_input(
        {
            "upstream_source": "leapseconds",
            "upstream_data_type": "spice",
            "upstream_descriptor": "historical",
            "required": True,
            "kickoff_job": False,
        }
    )


def test_validate_node_dict_valid():
    """Test that valid dict-formatted nodes instantiate without error."""
    format_upstream_node_input(
        {
            "upstream_source": "codice",
            "upstream_data_type": "l1a",
            "upstream_descriptor": "all",
            "required": True,
            "kickoff_job": False,
        }
    )


def test_validate_node_dict_with_defaults():
    """Test that dict nodes with omitted optional fields instantiate without error."""
    format_upstream_node_input(
        {
            "upstream_source": "leapseconds",
            "upstream_data_type": "spice",
            "upstream_descriptor": "historical",
        }
    )


def test_validate_node_dict_with_date_range():
    """Test that dict nodes with date range instantiate without error."""
    format_upstream_node_input(
        {
            "upstream_source": "hi",
            "upstream_data_type": "l1b",
            "upstream_descriptor": "45sensor-goodtimes",
            "date_range": ["-3p", "3p"],
        }
    )


def test_validate_node_dict_missing_required_key():
    """Test that dict missing required key raises ValueError."""
    with pytest.raises(ValueError, match="must contain keys"):
        format_upstream_node_input(
            {"upstream_source": "codice", "upstream_descriptor": "all"}
        )


def test_validate_node_not_list_or_dict():
    """Test that non-dict raises ValueError."""
    with pytest.raises(ValueError, match=r"Node must be a dict|must contain keys"):
        format_upstream_node_input("not_a_dict")


def test_validate_node_legacy_list_wrong_length():
    """Test that dict missing required key raises ValueError."""
    with pytest.raises(ValueError, match=r"Node must be a dict|must contain keys"):
        format_upstream_node_input(
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
            }
        )


def test_validate_node_invalid_source():
    """Test that invalid source raises ValueError."""
    with pytest.raises(ValueError, match="Invalid data source"):
        format_upstream_node_input(
            {
                "upstream_source": "invalid_source",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            }
        )


def test_validate_node_invalid_data_type():
    """Test that invalid data type raises ValueError."""
    with pytest.raises(ValueError, match="Invalid data type"):
        format_upstream_node_input(
            {
                "upstream_source": "codice",
                "upstream_data_type": "invalid_type",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            }
        )


def test_validate_node_empty_descriptor():
    """Test that empty descriptor raises ValueError."""
    with pytest.raises(ValueError, match="non-empty string"):
        format_upstream_node_input(
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "",
                "required": True,
                "kickoff_job": True,
            }
        )


def test_validate_node_dict_empty_descriptor():
    """Test that dict with empty descriptor raises ValueError."""
    with pytest.raises(ValueError, match="non-empty string"):
        format_upstream_node_input(
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "",
            }
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
    config = DependencyConfigReader().config

    # Verify config is loaded and not empty
    assert len(config) > 0


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
    config = DependencyConfigReader().config

    # Check that SWE L1A all descriptor has expected dependencies
    l1a_potential_job_node = ("swe", "l1a", "all")
    l1b_potential_job_node = ("swe", "l1b", "sci")
    l2_potential_job_node = ("swe", "l2", "sci")
    l3_potential_job_node = ("swe", "l3", "sci")
    assert l1a_potential_job_node in config
    assert l1b_potential_job_node in config
    assert l2_potential_job_node in config
    assert l3_potential_job_node in config

    # Check that upstream is what we expected
    l1a_potential_job_upstream_deps = config[l1a_potential_job_node]
    assert len(l1a_potential_job_upstream_deps) == 3

    # Now check that upstream dependencies are what we expected for
    # (swe, l1a, all)
    l0_upstream_dependency = l1a_potential_job_upstream_deps[0]
    leapseconds_upstream_dependency = l1a_potential_job_upstream_deps[1]
    spacecraft_clock_upstream_dependency = l1a_potential_job_upstream_deps[2]
    assert l0_upstream_dependency.source == "swe"
    assert l0_upstream_dependency.data_type == "l0"
    assert l0_upstream_dependency.descriptor == "raw"
    assert l0_upstream_dependency.required is True
    assert l0_upstream_dependency.kickoff_job is True
    assert l0_upstream_dependency.date_range is None

    assert leapseconds_upstream_dependency.source == "leapseconds"
    assert leapseconds_upstream_dependency.data_type == "spice"
    assert leapseconds_upstream_dependency.descriptor == "historical"
    assert leapseconds_upstream_dependency.required is True
    assert leapseconds_upstream_dependency.kickoff_job is False
    assert leapseconds_upstream_dependency.date_range is None

    assert spacecraft_clock_upstream_dependency.source == "spacecraft_clock"
    assert spacecraft_clock_upstream_dependency.data_type == "spice"
    assert spacecraft_clock_upstream_dependency.descriptor == "historical"
    assert spacecraft_clock_upstream_dependency.required is True
    assert spacecraft_clock_upstream_dependency.kickoff_job is False
    assert spacecraft_clock_upstream_dependency.date_range is None
