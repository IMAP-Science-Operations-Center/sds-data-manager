"""Shared static dependency-yaml constants for tests/scripts."""

import pytest
import yaml

from sds_data_manager.orchestration import dependency as dependency_module

# Captured before any patching happens, since dependency.py's "yaml" module is
# the same module object as the one imported here. Patching
# "dependency.yaml.safe_load" therefore replaces yaml.safe_load globally, so the
# side effect below must call this real reference instead of yaml.safe_load
# directly or it will recurse into the mock and blow the stack.
_REAL_SAFE_LOAD = yaml.safe_load

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

# A minimal mag chain (l1a -> l2), starting at major_version 1. The
# cross-instrument dependent lives in SWAPI_VALID_YAML below - a job's source
# always comes from the file it's defined in (see
# DependencyConfigReader._load_all_dependencies), never from its
# "outputs.source", so it can't live here.
_MAG_VALID_YAML = """
(l1a, all):
  inputs:
    - source: mag
      data_type: l0
      descriptor: raw
  outputs:
    - source: mag
      data_type: l1a
      descriptor: norm-magi
      major_version: 1

(l2, norm-srf):
  inputs:
    - source: mag
      data_type: l1a
      descriptor: norm-magi
  outputs:
    - source: mag
      data_type: l2
      descriptor: norm-rtn
      major_version: 1

"""

# Bump mag l2 norm-rtn ahead of swapi l3a alpha-sw (still major_version 1 in
# SWAPI_VALID_YAML below), to check that a cross-instrument dependent doesn't
# get validated against it.
MAG_VALID_YAML_L2_BUMP = _MAG_VALID_YAML.replace(
    "data_type: l2\n      descriptor: norm-rtn\n      major_version: 1",
    "data_type: l2\n      descriptor: norm-rtn\n      major_version: 2",
)

# swapi l3a alpha-sw depends on mag l2 norm-rtn - a real cross-instrument
# dependency (see imap_swapi_dependencies.yaml).
SWAPI_VALID_YAML = """
(l3a, alpha-sw):
  inputs:
    - source: mag
      data_type: l2
      descriptor: norm-rtn
  outputs:
    - source: swapi
      data_type: l3a
      descriptor: alpha-sw
      major_version: 1

"""

# A minimal, static swe chain (l1a -> l1b -> l2 -> l3), all starting at
# major_version 1.
_SWE_VALID_YAML = """
(l1a, all):
  inputs:
    - source: swe
      data_type: l0
      descriptor: raw
  outputs:
    - source: swe
      data_type: l1a
      descriptor: sci
      major_version: 1

(l1b, sci):
  inputs:
    - source: swe
      data_type: l1a
      descriptor: sci
  outputs:
    - source: swe
      data_type: l1b
      descriptor: sci
      major_version: 1

(l2, sci):
  inputs:
    - source: swe
      data_type: l1b
      descriptor: sci
  outputs:
    - source: swe
      data_type: l2
      descriptor: sci
      major_version: 1

(l3, sci):
  inputs:
    - source: swe
      data_type: l2
      descriptor: sci
  outputs:
    - source: swe
      data_type: l3
      descriptor: sci
      major_version: 1

"""

# Bump each level's major_version by a different, increasing amount to exercise
# a real multi-hop monotonic chain.
SWE_VALID_YAML_BUMPED = (
    _SWE_VALID_YAML.replace(
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


def mock_yaml(overrides):
    """Build a yaml.safe_load side_effect that swaps in fixture content per instrument.

    DependencyConfigReader loads every instrument's YAML file from disk. `overrides`
    is a dict of {instrument: content}; this intercepts only the read of
    imap_<instrument>_dependencies.yaml for each entry, letting every other
    instrument's file load normally.
    """

    def _side_effect(stream):
        name = getattr(stream, "name", "")
        for instrument, content in overrides.items():
            if f"imap_{instrument}_dependencies.yaml" in name:
                return _REAL_SAFE_LOAD(content)
        return _REAL_SAFE_LOAD(stream)

    return _side_effect


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
