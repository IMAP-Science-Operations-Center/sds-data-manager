"""Tests for the dependency configuration this repository runs with.

The YAML files and the reader live in imap-data-access; these tests check the
parts of the configuration that this repository's code relies on.
"""

import pytest
from imap_data_access.dependencies import (
    SPICE_SOURCES,
    DependencyConfigReader,
    DependencyNode,
)

from sds_data_manager.lambda_code.SDSCode.spice_utilities import KernelCollection


def test_spice_sources_match_kernel_collection():
    """imap-data-access's SPICE source list must match the metakernel builder's.

    The dependency node types live in imap-data-access, which can't import
    KernelCollection, so it keeps its own copy of the list.
    """
    assert list(SPICE_SOURCES) == KernelCollection().file_types


def test_get_node_for_output():
    """Test get_node_for_output."""
    reader = DependencyConfigReader()
    node = reader.get_node_for_output(DependencyNode("idex", "l1a", "sci-10days"))
    # Check that the correct processingJobNode was returned.
    assert node.source == "idex"
    assert node.data_type == "l1a"
    assert node.descriptor == "all"


def test_get_node_for_output_invalid():
    """Test get_node_for_output with non-existent dependency node."""
    reader = DependencyConfigReader()
    # This DependencyNode does not exist in the dependency configuration,
    # so it should raise an error.
    with pytest.raises(ValueError, match="No job found that produces output"):
        reader.get_node_for_output(DependencyNode("idex", "l1a", "sci"))


def test_get_node_for_input():
    """Test get_node_for_input."""
    reader = DependencyConfigReader()
    nodes = reader.get_nodes_for_input(DependencyNode("idex", "l1a", "sci-10days"))
    # There should be only one node
    assert len(nodes) == 1
    # Check that the correct processingJobNode was returned.
    assert nodes[0].source == "idex"
    assert nodes[0].data_type == "l1b"
    assert nodes[0].descriptor == "sci-10days"


def test_get_node_for_input_invalid():
    """Test get_node_for_input with non-existent dependency node."""
    reader = DependencyConfigReader()
    # This DependencyNode does not exist in the dependency configuration,
    # so it should return an empty list
    node = reader.get_nodes_for_input(DependencyNode("idex", "l1a", "sci"))
    assert node == []


def test_get_nodes_for_input_many():
    """Test get_node_for_input when there are multiple nodes."""
    reader = DependencyConfigReader()
    # Ultra l1c spacecraft psets are the inputs to multiple jobs
    nodes = reader.get_nodes_for_input(
        DependencyNode("ultra", "l1c", "45sensor-spacecraftpset")
    )
    # There should be 9 nodes
    assert len(nodes) == 9
