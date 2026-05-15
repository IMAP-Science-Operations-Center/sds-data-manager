"""Simple utilities for reading dependency configurations."""

import logging
from pathlib import Path

import yaml
from imap_data_access import VALID_INSTRUMENTS

from .utils import DependencyNode, UpstreamDependencyNode, format_upstream_node_input

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class DependencyConfigReader:
    """Dependency configuration reader.

    This class encapsulates all operations for reading instrument dependency
    configurations, including loading from YAML files, validating nodes.
    """

    def __init__(self):
        """Initialize DependencyConfig by loading all dependencies."""
        self._config = self._load_all_dependencies()

    @property
    def config(self) -> dict[tuple[str, str, str], list[DependencyNode]]:
        """Get the underlying dependency configuration dictionary.

        Returns
        -------
        dict[tuple[str, str, str], list[DependencyNode]]
            Mapping of ``(source, data_type, descriptor)`` tuples to lists of
            :class:`~.utils.DependencyNode` upstream dependency objects.
        """
        return self._config

    def _load_all_dependencies(
        self,
    ) -> dict[tuple[str, str, str], list[DependencyNode]]:
        """Load all instrument YAML dependency files and unified dependency.

        Returns a dictionary where each key is a parent node
        (source, data_type, descriptor) representing a downstream product,
        and each value is a list of upstream :class:`~.utils.DependencyNode`
        objects.

        Returns
        -------
        dict[tuple[str, str, str], list[DependencyNode]]
            Unified dependency configuration with structure:
            ``{(source, data_type, descriptor): [DependencyNode, ...]}``

        Raises
        ------
        FileNotFoundError
            If any expected YAML file is missing.
        ValueError
            If YAML content is invalid or empty.

        Examples
        --------
        >>> reader = DependencyConfigReader()
        >>> nodes = reader.config[('codice', 'l1a', 'all')]
        >>> nodes[0]
        DependencyNode(source='codice', data_type='l0', descriptor='raw', ...)
        """
        dependencies = {}
        yaml_dir = Path(__file__).parent

        for instrument in VALID_INSTRUMENTS:
            yaml_file = (
                yaml_dir / "dependencies" / f"imap_{instrument}_dependencies.yaml"
            )

            if instrument == "ialirt":
                continue

            if not yaml_file.exists():
                raise FileNotFoundError(
                    f"Dependency configuration file not found for '{instrument}' "
                    f"at {yaml_file}"
                )

            with open(yaml_file) as f:
                instrument_config = yaml.safe_load(f)

            if not instrument_config:
                raise ValueError(
                    f"Dependency content is empty for '{instrument}' in {yaml_file}"
                )

            # Parse YAML keys to construct (source, data_type, descriptor) tuples
            for key_str, upstream_list in instrument_config.items():
                # Skip any anchor definitions (common dependency groups).
                if not key_str.startswith("("):
                    continue

                try:
                    # Extract data_type and descriptor from key string
                    key_parts = key_str.strip("()").split(",")
                    data_type = key_parts[0].strip()
                    descriptor = key_parts[1].strip()
                    # Convert string key like "(l1a, all)" in the YAML to tuple
                    # (<instrument>,l1a, all) by combining with instrument source
                    # to get full downstream node.
                    # Validate the downstream product node by constructing a
                    # DependencyNode (validation runs in __post_init__).
                    DependencyNode(
                        source=instrument,
                        data_type=data_type,
                        descriptor=descriptor,
                    )
                    potential_job_node = (instrument, data_type, descriptor)

                    flattened_upstream_deps = self.recursive_flatten_list(upstream_list)

                    upstream_deps_nodes = []
                    # Validate each upstream node
                    for upstream in flattened_upstream_deps:
                        upstream_node = format_upstream_node_input(upstream)
                        upstream_deps_nodes.append(upstream_node)

                    dependencies[potential_job_node] = upstream_deps_nodes

                except (ValueError, IndexError) as e:
                    raise ValueError(
                        f"Non-product key error: '{key_str}' in {yaml_file}: {e}"
                    ) from e

        return dependencies

    def recursive_flatten_list(self, nested_list):
        """Recursively flatten a nested list structure.

        Multiple inheritance in dependency YAML files can result in
        lists containing other lists, which this method flattens.

        For example:
        spice_basic: &spice_basic
            - upstream_source: leapseconds
                upstream_data_type: spice
                upstream_descriptor: historical
                kickoff_job: false
            - upstream_source: spacecraft_clock
                upstream_data_type: spice
                upstream_descriptor: historical
                kickoff_job: false

        spice_45sensor_l1b: &spice_45sensor_l1b
            - *spice_basic
            - upstream_source: imap_frames
                upstream_data_type: spice
                upstream_descriptor: historical
                kickoff_job: false

        (l1b, 45sensor-de):
            - *spice_45sensor_l1b
            - upstream_source: hi
                upstream_data_type: l1a
                upstream_descriptor: 45sensor-de

        Parameters
        ----------
        nested_list : list
            A potentially nested list of dependencies.

        Returns
        -------
        list
            A single flattened list of dependencies.
        """
        flat_list = []
        for item in nested_list:
            if isinstance(item, list):
                # If the item is a list, extend with the flattened version of that list
                flat_list.extend(self.recursive_flatten_list(item))
            else:
                # Otherwise, append the item (which can be any object)
                flat_list.append(item)
        return flat_list


class DependencyResolver:
    """Get upstream and downstream dependencies for data products."""

    # Read in dependency config files
    _config = DependencyConfigReader().config

    def get_downstream_dependency_nodes(self, input_node: DependencyNode) -> list:
        """Get downstream dependency nodes for a given input node.

        Parameters
        ----------
        input_node : DependencyNode
            Then input node contains information such as source, data_type, descriptor.

        Returns
        -------
        list
            A list of downstream dependency nodes that depend on the input node.
        """
        return []

    def get_upstream_dependency(
        self, session, input_upstream_node: UpstreamDependencyNode
    ):
        """Get upstream dependencies for a given upstream node.

        UpstreamDependencyNode contains required Inputs:
            Source
            Data_type
            descriptor
            Start_time: yyyymmddhhmmss
            End_time: yyyymmddhhmmss

        Responsibilities:
            - Lookup upstream dependencies
            - Find all relevant files for upstream dependencies
            - Determine if it's a complete list
                Scenarios causing incompleteness:
                    1. Missing files in the database.
                    2. (Not supported yet) Due to anomaly (e.g., LOI, TCM, solar wind).
                    3. (Not supported yet) Due to repoint data delay or downlink delay.
                    4. If required dependencies missing or job IN PROGRESS.

        Parameters
        ----------
        session : Session
            Database session for querying dependencies and files.
        input_upstream_node : UpstreamDependencyNode
            The input upstream node with source, data_type, descriptor,
            and date range.

        Returns
        -------
        dict
            A dictionary with status code, message, and data.
            The data contains serialized upstream dependencies for
            job submission.
        """
        return {"status": 200, "message": "Success", "data": {}}
