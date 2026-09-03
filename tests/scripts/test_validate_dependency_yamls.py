"""Tests for validate_dependency_yamls.py."""

from unittest.mock import patch

import pytest

from sds_data_manager.orchestration import dependency as dependency_module
from scripts.dependency.validate_dependency_yamls import (
    validate_dependency_yaml_versions,
)
from sds_data_manager.orchestration.dependency import DependencyConfigReader
from tests.scripts.conftest import (
    IDEX_INVALID_YAML,
    IDEX_VALID_YAML,
    MAG_VALID_YAML_L2_BUMP,
    SWAPI_VALID_YAML,
    SWE_VALID_YAML_BUMPED,
    mock_yaml,
)

# Captured before any patching happens, since dependency.py's "yaml" module is
# the same module object as the one imported here. Patching
# "dependency.yaml.safe_load" therefore replaces yaml.safe_load globally, so the
# side effect below must call this real reference instead of yaml.safe_load
# directly or it will recurse into the mock and blow the stack.
_REAL_SAFE_LOAD = yaml.safe_load


@pytest.fixture(autouse=True)
def _clear_dependency_cache():
    """Force every test here to re-parse the dependency YAML.

    DependencyConfigReader caches its parsed config process-wide, so these
    tests -- which patch the YAML content -- need the cache cleared going in,
    and cleared again afterwards so their fake config does not leak into other
    test modules.
    """
    dependency_module.clear_config_cache()
    yield
    dependency_module.clear_config_cache()



def test_validate_dependency_yaml_versions_invalid():
    """Yaml with one invalid downstream major_version should raise."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"idex": IDEX_INVALID_YAML}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("idex", "l1a", "all")]

        with pytest.raises(ValueError, match="has major_version 0"):
            validate_dependency_yaml_versions(reader, 0, kickoff_job)


def test_validate_dependency_yaml_versions_valid():
    """Idex yaml content should pass."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"idex": IDEX_VALID_YAML}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("idex", "l1a", "all")]

        # Should not raise.
        validate_dependency_yaml_versions(reader, 0, kickoff_job)


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
        validate_dependency_yaml_versions(reader, 0, kickoff_job)


def test_validate_dependency_yaml_versions_swe():
    """A static swe chain, with a monotonic version bump per level, should pass."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"swe": SWE_VALID_YAML_BUMPED}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("swe", "l1a", "all")]

        # Should not raise.
        validate_dependency_yaml_versions(reader, 0, kickoff_job)
