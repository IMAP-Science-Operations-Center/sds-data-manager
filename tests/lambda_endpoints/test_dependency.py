"""Test data dependency functions."""

import json
from unittest.mock import patch

import pytest

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import dependency


def test_get_downstream_dependencies():
    "Tests get_downstream_dependencies function."
    event = {
        "dependency_type": "DOWNSTREAM",
        "relationship": "HARD",
        "data_source": "hit",
        "data_type": "l1a",
        "descriptor": "count-rates",
    }

    dependency_response = dependency.lambda_handler(event, None)
    dependents = json.loads(dependency_response["body"])

    expected_complete_dependent = [
        {
            "data_source": "hit",
            "data_type": "l1b",
            "descriptor": "all",
        }
    ]
    assert len(dependents) == 1

    assert dependents == expected_complete_dependent


def test_lambda_handler_no_dependencies():
    """Test lambda_handler when no dependencies are found."""
    event = {
        "data_source": "nonexistent",
        "data_type": "l0",
        "descriptor": "raw",
        "dependency_type": "UPSTREAM",
        "relationship": "HARD",
    }

    response = dependency.lambda_handler(event, None)

    assert response["statusCode"] == 200
    assert response["body"] == "[]"


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency.get_dependencies"
)
def test_lambda_handler_invalid_dependency_type(mock_get_dependencies):
    """Test lambda_handler when invalid dependency type is provided."""
    event = {
        "data_source": "jim",
        "data_type": "l0",
        "descriptor": "raw",
        "dependency_type": "INVALID",
        "relationship": "HARD",
    }
    mock_get_dependencies.return_value = None

    response = dependency.lambda_handler(event, None)

    assert response["statusCode"] == 500
    assert response["body"] == "Failed to load dependencies"


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
