"""Simple utilities for reading dependency configurations."""

from pathlib import Path

import yaml
from imap_data_access import VALID_INSTRUMENTS

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.utils import (
    DependencyNode,
)

from . import VALID_CADENCE_STRS
from .dependency import DataSource, DataType


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
        """Load all instrument YAML dependency files and unified dependency.

        Returns a dictionary where each key is a parent node
        (source, data_type, descriptor) representing a downstream product,
        and each value is a list of upstream dependencies as child nodes.

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
                # Convert string key like "(l1a, all)" to tuple (l1a, all)
                # and combine with instrument source to get full downstream node
                if key_str.startswith("#") or key_str.startswith("_"):
                    continue

                try:
                    # Extract data_type and descriptor from key string
                    key_parts = key_str.strip("()").split(",")
                    data_type = key_parts[0].strip()
                    descriptor = key_parts[1].strip()
                    downstream_node = (instrument, data_type, descriptor)

                    # Flatten upstream dependencies from YAML aliases. Eg.
                    #   (l1a, all):
                    #         - (hi, l0, raw, true, true)
                    #         - *spice_basic
                    # It is read in as list of lists.
                    flattened_upstream_deps = []
                    for item in upstream_list:
                        if isinstance(item, list):
                            flattened_upstream_deps.extend(item)
                        else:
                            flattened_upstream_deps.append(item)

                    # Validate each upstream node - they come as lists from YAML
                    for upstream in flattened_upstream_deps:
                        if isinstance(upstream, list):
                            self.validate_node(list(upstream))

                    dependencies[downstream_node] = flattened_upstream_deps

                except (ValueError, IndexError) as e:
                    raise ValueError(
                        f"Non-product key error: '{key_str}' in {yaml_file}: {e}"
                    ) from e

        return dependencies

    def validate_node(self, node: list) -> bool:
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
        self._validate_node_length(node)
        source, data_type, descriptor, required, kickoff_job, date_range = (
            self._unpack_node(node)
        )
        self._validate_boolean_fields(required, kickoff_job)
        self._validate_date_range(date_range)
        self._validate_source(source)
        self._validate_data_type(data_type)
        self._validate_descriptor(descriptor)
        return True

    def _validate_node_length(self, node: list) -> None:
        """Validate node has correct length."""
        if not isinstance(node, tuple) or len(node) < 5 or len(node) > 6:
            raise ValueError(
                f"Node must be a 5-element tuple "
                f"(source, data_type, descriptor, required, "
                f"kickoff_job), or a 6-element tuple with "
                f"(past, future) dates, got {node}"
            )

    def _unpack_node(self, node):
        """Unpack node into components."""
        print(f"Validating node: {node}")
        if len(node) == 5:
            source, data_type, descriptor, required, kickoff_job = node
            date_range = None
        else:
            source, data_type, descriptor, required, kickoff_job, date_range = node
        return source, data_type, descriptor, required, kickoff_job, date_range

    def _validate_boolean_fields(self, required: bool, kickoff_job: bool) -> None:
        """Validate required and kickoff_job are booleans."""
        if not isinstance(required, bool) or not isinstance(kickoff_job, bool):
            raise ValueError("'required' and 'kickoff_job' must be boolean values")

    def _validate_date_range(self, date_range) -> None:
        """Validate date range format if provided."""
        if not date_range:
            return

        past, future = date_range
        date_range_options = ["p", "h", "d", "l"]
        past_option = past[-1] if past else None
        future_option = future[-1] if future else None
        past_int = int(past[:-1]) if past else None
        future_int = int(future[:-1]) if future else None

        if (past_option and past_option not in date_range_options) or (
            past_int and past_int > 0
        ):
            raise ValueError(
                f"Invalid past '{past}'. Must end with "
                f"{date_range_options} and be negative."
            )
        if (future_option and future_option not in date_range_options) or (
            future_int and future_int < 0
        ):
            raise ValueError(
                f"Invalid future '{future}'. Must end with "
                f"{date_range_options} and be positive."
            )

    def _validate_source(self, source: str) -> None:
        """Validate source is valid."""
        if source not in self._data_source_validator.valid_source:
            raise ValueError(
                f"Invalid data source '{source}'. "
                f"Valid sources: {self._data_source_validator.valid_source}"
            )

    def _validate_data_type(self, data_type: str) -> None:
        """Validate data type is valid."""
        if data_type not in self._data_type_validator.valid_type:
            raise ValueError(
                f"Invalid data type '{data_type}'. "
                f"Valid types: {self._data_type_validator.valid_type}"
            )

    def _validate_descriptor(self, descriptor: str) -> None:
        """Validate descriptor is a non-empty string."""
        # TODO: validate descriptor once we finalize the descriptor list
        # for each instrument and data type.
        if not isinstance(descriptor, str) or not descriptor.strip():
            raise ValueError(
                f"Descriptor must be a non-empty string, got '{descriptor}'"
            )

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

    def get_downstream_dependency_nodes(self, dependency_node: DependencyNode) -> list:
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
            upstream_deps = [
                tuple(upstream_node[:3]) for upstream_node in upstream_nodes
            ]
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
