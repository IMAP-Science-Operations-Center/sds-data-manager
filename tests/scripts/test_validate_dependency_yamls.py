"""Tests for validate_dependency_yamls.py."""

from contextlib import nullcontext
from unittest.mock import patch

import pytest

from scripts.dependency.validate_dependency_yamls import (
    CHECKS,
    IdenticalDescriptorCheck,
    MajorVersionIncrementCheck,
    MajorVersionNoDecreaseCheck,
    MatchingOutputNodeCheck,
    SingleOutputCheck,
    validate_dependency_yaml_versions,
)
from sds_data_manager.orchestration.dependency import (
    DependencyConfigReader,
    clear_config_cache,
)
from tests.scripts.conftest import (
    _SWE_INVALID_SINGLE_OUTPUT_YAML,
    IDEX_INVALID_CATLST_YAML,
    IDEX_INVALID_YAML,
    IDEX_VALID_YAML,
    MAG_VALID_YAML_L2_BUMP,
    SWAPI_INVALID_YAML,
    SWAPI_VALID_YAML,
    SWE_VALID_YAML_BUMPED,
    mock_yaml,
)

checks = [check() for check in CHECKS.values()]


@pytest.mark.parametrize(
    ("check_objs", "expectation"),
    [
        (
            checks,
            pytest.raises(
                ValueError,
                match=r"Output \(idex, l1b, sci-10days\) has major_version 0."
                r" It should be greater than or equal to 1",
            ),
        ),  # If all the checks are running it should raise a value error
        (
            [
                check
                for check in checks
                if not isinstance(check, MajorVersionIncrementCheck)
            ],
            nullcontext(),
        ),  # If check 0 (MajorVersionIncrementCheck) is not "on" it should pass
    ],
)
def test_validate_dependency_yaml_versions_invalid(check_objs, expectation):
    """Yaml with one invalid downstream major_version should raise."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"idex": IDEX_INVALID_YAML}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("idex", "l1a", "all")]

        with expectation:
            validate_dependency_yaml_versions(reader, check_objs, kickoff_job)


def test_validate_dependency_yaml_versions_valid():
    """Idex yaml content should pass."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"idex": IDEX_VALID_YAML}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("idex", "l1a", "all")]

        # Should not raise.
        validate_dependency_yaml_versions(reader, checks, kickoff_job)


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
        validate_dependency_yaml_versions(reader, checks, kickoff_job)


def test_validate_dependency_yaml_versions_swe():
    """A static swe chain, with a monotonic version bump per level, should pass."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"swe": SWE_VALID_YAML_BUMPED}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("swe", "l1a", "all")]

        # Should not raise.
        validate_dependency_yaml_versions(reader, checks, kickoff_job)


@pytest.mark.parametrize(
    ("check_objs", "expectation"),
    [
        (
            checks,
            pytest.raises(
                ValueError,
                match="All outputs with identical descriptors "
                "should have the same major versions",
            ),
        ),  # If all the checks are running it should raise a value error
        (
            [
                check
                for check in checks
                if not isinstance(check, IdenticalDescriptorCheck)
            ],
            nullcontext(),
        ),  # If check 1 (IdenticalDescriptorCheck) is not "on" it should pass
    ],
)
def test_validate_dependency_yaml_invalid_idex_catlst_products(check_objs, expectation):
    """Check that outputs sharing a descriptor with different major versions raise."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"idex": IDEX_INVALID_CATLST_YAML}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("idex", "l1a", "all")]

        # IDEX_INVALID_CATLST_YAML is invalid; outputs sharing a descriptor must
        # share the same major_version.
        with expectation:
            validate_dependency_yaml_versions(reader, check_objs, kickoff_job)


@pytest.mark.parametrize(
    ("check_objs", "expectation"),
    [
        (
            checks,
            pytest.raises(
                ValueError,
                match="At least one output must have the same"
                " data level as the job node itself",
            ),
        ),  # If all the checks are running it should raise a value error
        (
            [
                check
                for check in checks
                if not isinstance(check, MatchingOutputNodeCheck)
            ],
            nullcontext(),
        ),  # If check 2 (MatchingOutputNodeCheck) is not "on" it should pass
    ],
)
def test_validate_dependency_yaml_invalid_data_levels(check_objs, expectation):
    """Check that an error is thrown when no output shares the job node's data level."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"swapi": SWAPI_INVALID_YAML}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("swapi", "l3a", "alpha-sw")]

        with expectation:
            validate_dependency_yaml_versions(reader, check_objs, kickoff_job)


def test_validate_dependency_yaml_decreased_mv():
    """Check that an error is thrown when a node has a decreased major version."""
    # Set up the old and new reader.
    old_reader = DependencyConfigReader()
    clear_config_cache()
    new_reader = DependencyConfigReader()
    # Update the new reader to have a decreased major version for the output of the
    # job node ("swe", "l1a", "all").
    new_reader.config[("swe", "l1a", "all")].outputs[0].major_version -= 1
    # Add this job node to the list of updated nodes so it gets checked.
    updated_nodes = [new_reader.config[("swe", "l1a", "all")]]
    check = [MajorVersionNoDecreaseCheck(updated_nodes, old_reader)]
    kickoff_job = new_reader.config[("swe", "l1a", "all")]

    with pytest.raises(ValueError, match=r"Major versions should never decrease\."):
        validate_dependency_yaml_versions(new_reader, check, kickoff_job)


@pytest.mark.parametrize(
    ("check_objs", "expectation"),
    [
        (
            checks,
            pytest.raises(
                ValueError,
                match="does not match the descriptor of its single output",
            ),
        ),  # If all the checks are running it should raise a value error
        (
            [check for check in checks if not isinstance(check, SingleOutputCheck)],
            nullcontext(),
        ),  # If check 4 (SingleOutputCheck) is not "on" it should pass
    ],
)
def test_validate_dependency_yaml_invalid_single_output_job(check_objs, expectation):
    """Check that an error is thrown when no output shares the job node's data level."""
    with patch(
        "sds_data_manager.orchestration.dependency.yaml.safe_load",
        side_effect=mock_yaml({"swe": _SWE_INVALID_SINGLE_OUTPUT_YAML}),
    ):
        reader = DependencyConfigReader()
        kickoff_job = reader.config[("swe", "l1a", "all")]

        with expectation:
            validate_dependency_yaml_versions(reader, check_objs, kickoff_job)
