"""Tests for reprocess_jobs_for_major_version_bump.py."""

from scripts.dependency.reprocess_jobs_for_major_version_bump import (
    reprocess_jobs_for_major_version_bump,
)
from sds_data_manager.orchestration.dependency import DependencyConfigReader


def test_reprocess_jobs_for_major_version_bump():
    """A single changed leaf job should be the only one reprocessed."""
    old_reader = DependencyConfigReader()
    new_reader = DependencyConfigReader()

    new_reader.config[("idex", "l1b", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2a", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-30days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-30days")].outputs[1].major_version += 1
    jobs = reprocess_jobs_for_major_version_bump(old_reader, new_reader)

    assert jobs == [
        ("idex", "l1b", "sci-10days"),
        ("idex", "l2b", "all-30days"),
    ]


def test_reprocess_jobs_for_major_version_bump_root_node():
    """When the kickoff job itself changed, it should be the reprocessed root."""
    old_reader = DependencyConfigReader()
    new_reader = DependencyConfigReader()

    new_reader.config[("idex", "l1a", "all")].outputs[0].major_version += 1
    new_reader.config[("idex", "l1b", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2a", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-30days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-30days")].outputs[1].major_version += 1
    jobs = reprocess_jobs_for_major_version_bump(old_reader, new_reader)

    assert jobs == [("idex", "l1a", "all"), ("idex", "l2b", "all-30days")]


def test_reprocess_jobs_for_major_version_bump_two_upstream():
    """A node with two changed upstream branches should reprocess both roots."""
    old_reader = DependencyConfigReader()
    new_reader = DependencyConfigReader()

    new_reader.config[("idex", "l1b", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l1b", "msg-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2a", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-30days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-30days")].outputs[1].major_version += 1
    jobs = reprocess_jobs_for_major_version_bump(old_reader, new_reader)

    assert jobs == [
        ("idex", "l1b", "msg-10days"),
        ("idex", "l1b", "sci-10days"),
        ("idex", "l2b", "all-30days"),
    ]


def test_reprocess_jobs_for_major_version_bump_multiple_instruments():
    """Changes in independent instrument pipelines should each get their own root."""
    old_reader = DependencyConfigReader()
    new_reader = DependencyConfigReader()

    # IDEX
    new_reader.config[("idex", "l1b", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2a", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-30days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-30days")].outputs[1].major_version += 1

    # SWE
    new_reader.config[("swe", "l1a", "all")].outputs[0].major_version += 1
    new_reader.config[("swe", "l1b", "sci")].outputs[0].major_version += 1
    new_reader.config[("swe", "l2", "sci")].outputs[0].major_version += 1

    jobs = reprocess_jobs_for_major_version_bump(old_reader, new_reader)

    assert jobs == [
        ("idex", "l1b", "sci-10days"),
        # since the inputs to idex l2b are non triggering inputs, we
        # also kick off l2b jobs. They should wait to run after everything
        # is finished due to the _check_for_running_dependencies check.
        ("idex", "l2b", "all-30days"),
        ("swe", "l1a", "all"),
    ]
