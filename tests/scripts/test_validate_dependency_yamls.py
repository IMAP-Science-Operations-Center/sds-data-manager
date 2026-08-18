"""Tests for validate_dependency_yamls.py."""

from unittest.mock import patch

import pytest
import yaml

from scripts.validate_dependency_yamls import validate_dependency_yaml_versions
from sds_data_manager.orchestration.dependency import DependencyConfigReader

# Captured before any patching happens, since dependency.py's "yaml" module is
# the same module object as the one imported here. Patching
# "dependency.yaml.safe_load" therefore replaces yaml.safe_load globally, so the
# side effect below must call this real reference instead of yaml.safe_load
# directly or it will recurse into the mock and blow the stack.
_REAL_SAFE_LOAD = yaml.safe_load

# Idex l1a sci-10days has the same major version than idex l1b
# sci-10 days (downstream) this is valid.
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
      major_version: 1
"""

# Idex l1a sci-10days now has a greater major version than idex l1b
# sci-10 days (downstream) this is invalid.
IDEX_INVALID_YAML = IDEX_VALID_YAML.replace(
    "data_type: l1b\n      descriptor: sci-10days\n      major_version: 1",
    "data_type: l1b\n      descriptor: sci-10days\n      major_version: 0",
)


def _mock_idex_yaml(content):
    """Build a yaml.safe_load side_effect that swaps in `content` for the idex file.

    DependencyConfigReader loads every instrument's YAML file from disk, so this
    intercepts only the read of imap_idex_dependencies.yaml and lets every other
    instrument's file load normally.
    """

    def _side_effect(stream):
        if "imap_idex_dependencies.yaml" in getattr(stream, "name", ""):
            return _REAL_SAFE_LOAD(content)
        return _REAL_SAFE_LOAD(stream)

    return _side_effect


def test_validate_dependency_yaml_versions_fan_out_invalid():
    """Yaml with one invalid downstream major_version should raise."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=_mock_idex_yaml(IDEX_INVALID_YAML),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("idex", "l1a", "all")]

        with pytest.raises(ValueError, match="has major_version 0"):
            validate_dependency_yaml_versions(reader, 0, kickoff_job)


def test_validate_dependency_yaml_versions_fan_out_valid():
    """Idex yaml content should pass."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=_mock_idex_yaml(IDEX_VALID_YAML),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("idex", "l1a", "all")]

        # Should not raise.
        validate_dependency_yaml_versions(reader, 0, kickoff_job)
