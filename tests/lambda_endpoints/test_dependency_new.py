"""Tests for dependency_new module.

This module provides unit tests for the DependencyConfigReader class used to
read and retrieve upstream dependencies from instrument YAML configuration files.
"""

from unittest.mock import MagicMock, mock_open, patch

import pytest

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.dependency_new import (  # noqa: E501
    DependencyConfigReader,
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


# Tests for validate_node
def test_validate_node_valid_instrument():
    """Test validation of valid instrument nodes."""
    config = DependencyConfigReader()
    assert (
        config.validate_node(
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            }
        )
        is True
    )
    assert (
        config.validate_node(
            {
                "upstream_source": "hi",
                "upstream_data_type": "l1b",
                "upstream_descriptor": "hi-counters-aggregated",
                "required": True,
                "kickoff_job": False,
            }
        )
        is True
    )


def test_validate_node_valid_spice():
    """Test validation of valid SPICE nodes."""
    config = DependencyConfigReader()
    assert (
        config.validate_node(
            {
                "upstream_source": "leapseconds",
                "upstream_data_type": "spice",
                "upstream_descriptor": "historical",
                "required": True,
                "kickoff_job": False,
            }
        )
        is True
    )


def test_validate_node_dict_valid():
    """Test validation of valid dict-formatted nodes."""
    config = DependencyConfigReader()
    # Dict with all required fields
    assert (
        config.validate_node(
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": False,
            }
        )
        is True
    )


def test_validate_node_dict_with_defaults():
    """Test validation of dict nodes with default required/kickoff_job."""
    config = DependencyConfigReader()
    # Dict without optional fields should use defaults
    assert (
        config.validate_node(
            {
                "upstream_source": "leapseconds",
                "upstream_data_type": "spice",
                "upstream_descriptor": "historical",
            }
        )
        is True
    )


def test_validate_node_dict_with_date_range():
    """Test validation of dict nodes with date range."""
    config = DependencyConfigReader()
    assert (
        config.validate_node(
            {
                "upstream_source": "hi",
                "upstream_data_type": "l1b",
                "upstream_descriptor": "45sensor-goodtimes",
                "date_range": ["-3p", "3p"],
            }
        )
        is True
    )


def test_validate_node_dict_missing_required_key():
    """Test that dict missing required key raises ValueError."""
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match="must contain keys"):
        config.validate_node(
            {"upstream_source": "codice", "upstream_descriptor": "all"}
        )


def test_validate_node_not_list_or_dict():
    """Test that non-dict raises ValueError."""
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match=r"Node must be a dict|must contain keys"):
        config.validate_node("not_a_dict")


def test_validate_node_legacy_list_wrong_length():
    """Test that non-dict raises ValueError."""
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match=r"Node must be a dict|must contain keys"):
        config.validate_node(
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
            }
        )


def test_validate_node_invalid_source():
    """Test that invalid source raises ValueError."""
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match="Invalid data source"):
        config.validate_node(
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
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match="Invalid data type"):
        config.validate_node(
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
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match="non-empty string"):
        config.validate_node(
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
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match="non-empty string"):
        config.validate_node(
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
