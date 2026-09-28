"""Tests for validate_dependency_yamls.py."""

from unittest.mock import patch

import pytest

from scripts.dependency.validate_dependency_yamls import (
    validate_dependency_yaml_versions,
)
from sds_data_manager.orchestration.dependency import DependencyConfigReader
from tests.scripts.conftest import (
    IDEX_INVALID_CATLST_YAML,
    IDEX_INVALID_YAML,
    IDEX_VALID_YAML,
    MAG_VALID_YAML_L2_BUMP,
    SWAPI_INVALID_YAML,
    SWAPI_VALID_YAML,
    SWE_VALID_YAML_BUMPED,
    mock_yaml,
)


def test_validate_dependency_yaml_versions_invalid():
    """Yaml with one invalid downstream major_version should raise."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"idex": IDEX_INVALID_YAML}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("idex", "l1a", "all")]

        with pytest.raises(ValueError, match="has major_version 0"):
            validate_dependency_yaml_versions(reader, kickoff_job)


def test_validate_dependency_yaml_versions_valid():
    """Idex yaml content should pass."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"idex": IDEX_VALID_YAML}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("idex", "l1a", "all")]

        # Should not raise.
        validate_dependency_yaml_versions(reader, kickoff_job)


def test_validate_dependency_yaml_versions_mag_l2():
    """Bumping mag l2 norm-rtn should pass, even though swapi depends on it.

    validate_dependency_yaml_versions only walks downstream jobs within the same
    source (see the `processing_node.source != node.source` check), so swapi's
    l3a alpha-sw job - a real cross-instrument dependent of mag l2 norm-rtn -
    should never be checked or cause this to raise.
    """
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml(
            {"mag": MAG_VALID_YAML_L2_BUMP, "swapi": SWAPI_VALID_YAML}
        ),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("mag", "l1a", "all")]

        # Should not raise, per the docstring above.
        validate_dependency_yaml_versions(reader, kickoff_job)


def test_validate_dependency_yaml_versions_swe():
    """A static swe chain, with a monotonic version bump per level, should pass."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"swe": SWE_VALID_YAML_BUMPED}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("swe", "l1a", "all")]

        # Should not raise.
        validate_dependency_yaml_versions(reader, kickoff_job)


def test_validate_dependency_yaml_invalid_idex_catlst_products():
    """Check that outputs sharing a descriptor with different major versions raise."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"idex": IDEX_INVALID_CATLST_YAML}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("idex", "l1a", "all")]

        # IDEX_INVALID_CATLST_YAML is invalid; outputs sharing a descriptor must
        # share the same major_version.
        error_msg = (
            "All outputs with identical descriptors should have the same major versions"
        )
        with pytest.raises(ValueError, match=error_msg):
            validate_dependency_yaml_versions(reader, kickoff_job)


def test_validate_dependency_yaml_invalid_data_levels():
    """Check that an error is thrown when no output shares the job node's data level."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"swapi": SWAPI_INVALID_YAML}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("swapi", "l3a", "alpha-sw")]

        error_msg = "At least one output must have the same data level as the job node"
        with pytest.raises(ValueError, match=error_msg):
            validate_dependency_yaml_versions(reader, kickoff_job)
