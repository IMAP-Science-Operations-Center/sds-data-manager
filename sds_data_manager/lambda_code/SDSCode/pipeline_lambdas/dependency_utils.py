"""Simple utilities for reading dependency configurations."""

from pathlib import Path
from typing import Optional

import yaml
from imap_data_access import VALID_INSTRUMENTS

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.utils import DependencyNode, UpstreamDependencyNode
from .dependency import DataSource, DataType
from . import VALID_CADENCE_STRS


# TODO: rename to DependencyConfig once we have these new feature
# is implemented for all use-cases and remove the old one code.
class DependencyConfigNew:
    """Manages dependency configuration loading and querying.
    
    This class encapsulates all operations for working with instrument dependency
    configurations, including loading from YAML files, validating nodes, and
    querying upstream/downstream dependencies.
    """

    def __init__(self):
        """Initialize DependencyConfig by loading all dependencies."""
        self._config = self._load_all_dependencies()
        self._data_source_validator = DataSource()
        self._data_type_validator = DataType()

    @property
    def config(self) -> dict:
        """Get the underlying dependency configuration dictionary."""
        return self._config

    def _load_all_dependencies(self) -> dict:
        """Load all instrument YAML dependency files and construct unified dependency graph.

        Returns a dictionary where each key is a parent node (source, data_type, descriptor)
        representing a downstream product, and each value is a list of upstream
        dependencies as child nodes.

        Returns
        -------
        dict
            Unified dependency configuration with structure:
            {(source, data_type, descriptor): [upstream_deps_list]}

        Raises
        ------
        FileNotFoundError
            If any expected YAML file is missing.
        ValueError
            If YAML content is invalid or empty.

        Examples
        --------
        >>> config = DependencyConfig()
        >>> config.config[('codice', 'l1a', 'all')]
        [('codice', 'l0', 'raw', True, True), ...]
        """
        dependencies = {}
        yaml_dir = Path(__file__).parent

        for instrument in VALID_INSTRUMENTS:
            yaml_file = yaml_dir / f"imap_{instrument}_dependencies.yaml"

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
                # Convert string key like "(l1a, all)" to tuple (l1a, all)
                # and combine with instrument source to get full downstream node
                if key_str.startswith("#") or key_str.startswith("_"):
                    # print(f"Skipping comment or non-product key: {key_str}")
                    continue
                try:
                    # Extract data_type and descriptor from key string
                    print(f"Parsing key: {key_str} for instrument: {instrument}")
                    key_parts = key_str.strip("()").split(",")
                    data_type = key_parts[0].strip()
                    descriptor = key_parts[1].strip()
                    downstream_node = (instrument, data_type, descriptor)
                    # print(f"upstream_list: {upstream_list}")
                    # for upstream in upstream_list:
                        # self.validate_node(tuple(upstream))
                    dependencies[downstream_node] = upstream_list
                    
                except (ValueError, IndexError) as e:
                    raise ValueError(
                        f"Non-product key error: '{key_str}' in {yaml_file}: {e}"
                    ) from e

        return dependencies

    def validate_node(self, node: tuple) -> bool:
        """Validate a dependency node.

        A valid node must have exactly 5 elements or 6 elements:
            (
                source,
                data_type,
                descriptor,
                required,
                kickoff_job,
                Optional(past, future)
            )
        If it includes past/future date ranges, it should follow the following format:
            - p - pointing
            - h - hourly
            - d - days
            - l - last_processed
            past and future should end with one of these options. Eg.
                ("-3p", "3pm") means 3 pointing
                ("-3d", "5d") means 5 days
                ("-2h", "2h") means 2 hours
                ("1l",) means last processed.
        Validation is performed using DataSource and DataType validators.

        Parameters
        ----------
        node : DependencyNode
            Node to validate.

        Returns
        -------
        bool
            True if node is valid.

        Raises
        ------
        ValueError
            If node format is invalid or contains invalid values.

        Examples
        --------
        >>> config = DependencyConfig()
        >>> config.validate_node(('codice', 'l1a', 'all'))
        True
        >>> config.validate_node(('invalid', 'l1a', 'all'))
        Traceback (most recent call last):
            ...
        ValueError: Invalid data source...
        """
        if not isinstance(node, tuple) or len(node) < 5 or len(node) > 6:
            raise ValueError(
                f"Node must be a 5-element tuple (source, data_type, descriptor, required, kickoff_job), "
                f"or a 6-element tuple (source, data_type, descriptor, required, kickoff_job, (past, future)), "
                f"got {node}"
            )

        print(f"Validating node: {node}")
        if len(node) == 5:
            source, data_type, descriptor, required, kickoff_job = node
        elif len(node) == 6:
            source, data_type, descriptor, required, kickoff_job, (past, future) = node

        # check that required and kickoff_job are booleans
        if not isinstance(required, bool) or not isinstance(kickoff_job, bool):
            raise ValueError(
                f"'required' and 'kickoff_job' must be boolean values"
            )

        # past and future should end with one of these options:
        # p - pointing
        # h - hourly
        # d - days
        # l - last_processed
        date_range_options = ["p", "h", "d", "l"]
        # parse last element in the string to get the option character
        past_option = past[-1] if past else None
        future_option = future[-1] if future else None
        # parse the integer part of past and future for further validation
        past_int = int(past[:-1]) if past else None
        future_int = int(future[:-1]) if future else None

        # check past format
        if past_option and past_option not in date_range_options or past_int > 0:
            raise ValueError(
                f"Invalid past value '{past}'. Must end with one of {date_range_options} and have a negative integer."
            )
        # check future format
        if future_option and future_option not in date_range_options or future_int < 0:
            raise ValueError(
                f"Invalid future value '{future}'. Must end with one of {date_range_options} and have a positive integer."
            )

        # validate source
        if source not in self._data_source_validator.valid_source:
            raise ValueError(
                f"Invalid data source '{source}'. "
                f"Valid sources: {self._data_source_validator.valid_source}"
            )

        # validate data type
        if data_type not in self._data_type_validator.valid_type:
            raise ValueError(
                f"Invalid data type '{data_type}'. "
                f"Valid types: {self._data_type_validator.valid_type}"
            )

        # Descriptor validation - just check it's a non-empty string
        if not isinstance(descriptor, str) or not descriptor.strip():
            raise ValueError(f"Descriptor must be a non-empty string, got '{descriptor}'")

        return True

    def get_cadence_job(self, descriptor: str) -> str | None:
        """Get cadence information from a descriptor.

        Cadence jobs are products at data level l2 or l2b whose descriptor contains
        cadence indicators like "1mo", "3mo", "6mo", or "1yr".

        Parameters
        ----------
        descriptor : str
            The descriptor to check for cadence indicators.
        
        Returns
        -------
        str or None
            The cadence string (e.g., '1mo', '3mo', '6mo', '1yr').
            Returns None if no cadence indicators are found.

        Examples
        --------
        >>> config = DependencyConfig()
        >>> config.get_cadence_job("swe-sci-1mo")
        '1mo'
        """
        # For given descriptor, parse cadence.
        cadence = descriptor.split("-")[-1]
        if descriptor.split("-")[-1] in VALID_CADENCE_STRS:
            return cadence

        return None

    def get_downstream_dependency_nodes(
        self, dependency_node: DependencyNode
    ) -> list:
        """Get downstream dependencies for a given node.

        Parameters
        ----------
        dependency_node : DependencyNode
            The node for which to retrieve downstream dependencies.

        Returns
        -------
        list
            List of downstream dependencies for the given node.

        Examples
        --------
        >>> config = DependencyConfig()
        >>> config.get_downstream_dependency_nodes(('swe', 'l1a', 'all'))
        [('swe', 'l1b', 'swe-all'), ...]
        """
        downstream_deps = []

        for downstream_node, upstream_nodes in self._config.items():
            # get node from upstream_nodes only since upstream_nodes has
            # extra info such as required, kickoff_job, etc.
            upstream_deps = [tuple(upstream_node[:3]) for upstream_node in upstream_nodes]
            if dependency_node in upstream_deps:
                downstream_deps.append(downstream_node)

        return downstream_deps

    # def get_upstream_dependency(self, dependency_node: UpstreamDependencyNode):
    #     """Get upstream dependencies for a given downstream product.

    #     Parameters
    #     ----------
    #     dependency_node : UpstreamDependencyNode
    #         The downstream node for which to retrieve upstream dependencies.

    #     Returns
    #     -------
    #     list
    #         List of upstream dependencies for the given node.

    #     Examples
    #     --------
    #     >>> config = DependencyConfig()
    #     >>> config.get_upstream_dependency(('swe', 'l1b', 'swe-all'))
    #     [('swe', 'l1a', 'all', True, True), ...]
    #     """
    #     # Lot more logics to implement here.
    #     pass
