"""Tests for the dependency node helpers in dagster_utilities.py."""

from dagster import AssetKey
from imap_data_access.dependencies import DependencyNode, ProcessingJobNode

from sds_data_manager.orchestration.dagster_utilities import (
    to_dagster_asset,
    to_dagster_name,
)


def test_to_dagster_name_removes_dashes():
    """Asset names join source, data type and descriptor, without dashes."""
    node = DependencyNode("hi", "l1b", "45sensor-goodtimes")
    assert to_dagster_name(node) == "hi_l1b_45sensorgoodtimes"
    assert to_dagster_asset(node) == AssetKey("hi_l1b_45sensorgoodtimes")


def test_to_dagster_name_for_a_job_node():
    """Job nodes are named the same way as dependency nodes."""
    job = ProcessingJobNode(
        source="swe",
        data_type="l1a",
        descriptor="all",
        inputs=[],
        outputs=[],
        partition="daily",
    )
    assert to_dagster_name(job) == "swe_l1a_all"
    assert to_dagster_asset(job) == AssetKey("swe_l1a_all")
