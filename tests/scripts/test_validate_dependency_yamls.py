"""Tests for validate_dependency_yamls.py."""

from unittest.mock import patch

import pytest
import yaml

from scripts.validate_dependency_yamls import validate_dependency_yaml_versions
from sds_data_manager.orchestration import dependency as dependency_module
from sds_data_manager.orchestration.dependency import DependencyConfigReader

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


# Idex l1b sci-10days (downstream) has a major_version greater than or equal to
# its upstream input, idex l1a sci-10days. This is valid.
IDEX_VALID_YAML = """
(l1a, all):
  inputs:
    - source: idex
      data_type: l0
      descriptor: raw
  outputs:
    - source: idex
      data_type: l1a
      descriptor: sci-10days
      major_version: 1

(l1b, sci-10days):
  inputs:
    - source: idex
      data_type: l1a
      descriptor: sci-10days
  outputs:
    - source: idex
      data_type: l1b
      descriptor: sci-10days
      major_version: 2
    - source: idex
      data_type: l1b
      descriptor: msg-10days
      major_version: 1

"""

# Idex l1a sci-10days now has a greater major version than idex l1b
# sci-10 days (downstream) this is invalid.
IDEX_INVALID_YAML = IDEX_VALID_YAML.replace(
    "data_type: l1b\n      descriptor: sci-10days\n      major_version: 2",
    "data_type: l1b\n      descriptor: sci-10days\n      major_version: 0",
)


def _mock_yaml(instrument, content):
    """Build a yaml.safe_load side_effect that swaps in `content` for one instrument.

    DependencyConfigReader loads every instrument's YAML file from disk, so this
    intercepts only the read of imap_<instrument>_dependencies.yaml and lets every
    other instrument's file load normally.
    """

    def _side_effect(stream):
        if f"imap_{instrument}_dependencies.yaml" in getattr(stream, "name", ""):
            return _REAL_SAFE_LOAD(content)
        return _REAL_SAFE_LOAD(stream)

    return _side_effect


def test_validate_dependency_yaml_versions_invalid():
    """Yaml with one invalid downstream major_version should raise."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=_mock_yaml("idex", IDEX_INVALID_YAML),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("idex", "l1a", "all")]

        with pytest.raises(ValueError, match="has major_version 0"):
            validate_dependency_yaml_versions(reader, 0, kickoff_job)


def test_validate_dependency_yaml_versions_valid():
    """Idex yaml content should pass."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=_mock_yaml("idex", IDEX_VALID_YAML),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("idex", "l1a", "all")]

        # Should not raise.
        validate_dependency_yaml_versions(reader, 0, kickoff_job)


def test_validate_dependency_yaml_versions_mag_l2():
    """Bumping mag l2 norm-rtn should pass, even though swapi depends on it.

    validate_dependency_yaml_versions only walks downstream jobs within the same
    source (see the `processing_node.source != node.source` check), so a
    cross-instrument dependent like (swapi, l3a, alpha-sw) should never be checked
    or cause this to raise.
    """
    with open(
        "sds_data_manager/orchestration/dependencies/imap_mag_dependencies.yaml",
        encoding="utf-8",
    ) as file:
        mag_valid_yaml = file.read()

    mag_valid_yaml_l2_bump = mag_valid_yaml.replace(
        "data_type: l2\n      descriptor: norm-rtn\n      major_version: 1",
        "data_type: l2\n      descriptor: norm-rtn\n      major_version: 2",
    )
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=_mock_yaml("mag", mag_valid_yaml_l2_bump),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("mag", "l1a", "all")]

        # Should not raise, per the docstring above.
        validate_dependency_yaml_versions(reader, 0, kickoff_job)


def test_validate_dependency_yaml_versions_swe():
    """The real swe dependency yaml, with a monotonic version bump per level, passes.

    Unlike the other tests here, this loads the actual production yaml instead of
    a hand-built fixture, as a regression check against the real config.
    """
    with open(
        "sds_data_manager/orchestration/dependencies/imap_swe_dependencies.yaml",
        encoding="utf-8",
    ) as file:
        valid_swe_yaml = file.read()

    valid_swe_yaml = (
        valid_swe_yaml.replace(
            "data_type: l1b\n      descriptor: sci\n      major_version: 1",
            "data_type: l1b\n      descriptor: sci\n      major_version: 5",
        )
        .replace(
            "data_type: l2\n      descriptor: sci\n      major_version: 1",
            "data_type: l2\n      descriptor: sci\n      major_version: 7",
        )
        .replace(
            "data_type: l3\n      descriptor: sci\n      major_version: 1",
            "data_type: l3\n      descriptor: sci\n      major_version: 10",
        )
    )

    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=_mock_yaml("swe", valid_swe_yaml),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("swe", "l1a", "all")]

        # Should not raise.
        validate_dependency_yaml_versions(reader, 0, kickoff_job)
