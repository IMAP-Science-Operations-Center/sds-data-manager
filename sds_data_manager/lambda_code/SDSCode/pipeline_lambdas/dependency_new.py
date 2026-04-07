"""Simple utilities for reading dependency configurations."""

import logging
from pathlib import Path

import yaml
from imap_data_access import VALID_INSTRUMENTS

from .dependency import DataSource, DataType

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Date range validation constants
NEAREST_OPTIONS = ("nd", "np")
DATE_RANGE_OPTIONS = ("p", "h", "d", "l", *NEAREST_OPTIONS)


class DependencyConfigReader:
    """Dependency configuration reader.

    This class encapsulates all operations for reading instrument dependency
    configurations, including loading from YAML files, validating nodes.
    """

    def __init__(self):
        """Initialize DependencyConfig by loading all dependencies."""
        self._data_source_validator = DataSource()
        self._data_type_validator = DataType()
        self._config = self._load_all_dependencies()

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
                # Skip any comments or anchor definitions (common dependency groups).
                # Product keys have format: (data_type, descriptor)
                # Anchor keys don't have parentheses, so skip them.
                if key_str.startswith("#") or not key_str.startswith("("):
                    continue

                try:
                    # Extract data_type and descriptor from key string
                    key_parts = key_str.strip("()").split(",")
                    data_type = key_parts[0].strip()
                    descriptor = key_parts[1].strip()
                    # Convert string key like "(l1a, all)" in the YAML to tuple
                    # (<instrument>,l1a, all) by combining with instrument source
                    # to get full downstream node.
                    self._validate_source(instrument)
                    self._validate_data_type(data_type)
                    self._validate_descriptor(descriptor)
                    downstream_node = (instrument, data_type, descriptor)

                    # Flatten upstream dependencies because of how YAML aliases are
                    # loaded in code. New YAML format loads dependencies as dicts:
                    #   (l1a, all):
                    #         - source: hi
                    #           data_type: l0
                    #           descriptor: raw
                    #         - *spice_basic  # YAML alias expands to list of dicts
                    # When aliased, it may be read as list of lists of dicts.
                    flattened_upstream_deps = []
                    for item in upstream_list:
                        # Handle YAML aliases that expand to list of dicts
                        if (
                            isinstance(item, list)
                            and item
                            and isinstance(item[0], dict)
                        ):
                            flattened_upstream_deps.extend(item)
                        else:
                            flattened_upstream_deps.append(item)

                    # Validate each upstream node
                    for upstream in flattened_upstream_deps:
                        self.validate_node(upstream)

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
            - nd - nearest day
            - np - nearest pointing

            past and future should end with one of these options. Eg.
                ("-3p", "3pm") means 3 pointing
                ("-3d", "5d") means 5 days
                ("-2h", "2h") means 2 hours
                ("1l",) means last processed
                ("6np",) means nearest 6 pointing

        Validation is performed for each field.

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

    def _validate_node_length(self, node) -> None:
        """Validate node has correct format (dict)."""
        # Accept only dictionary format
        if not isinstance(node, dict):
            raise ValueError(f"Node must be a dict, got {type(node).__name__}")

        required_keys = {
            "upstream_source",
            "upstream_data_type",
            "upstream_descriptor",
        }
        if not required_keys.issubset(node.keys()):
            raise ValueError(
                f"Node dict must contain keys {required_keys}, got {set(node.keys())}"
            )

    def _unpack_node(self, node):
        """Unpack node into components, handling both dict."""
        source = node["upstream_source"]
        data_type = node["upstream_data_type"]
        descriptor = node["upstream_descriptor"]
        required = node.get("required", True)
        kickoff_job = node.get("kickoff_job", True)
        date_range = node.get("date_range", None)
        return source, data_type, descriptor, required, kickoff_job, date_range

    def _validate_boolean_fields(self, required: bool, kickoff_job: bool) -> None:
        """Validate required and kickoff_job are booleans."""
        if not isinstance(required, bool) or not isinstance(kickoff_job, bool):
            raise ValueError("'required' and 'kickoff_job' must be boolean values")

    def _validate_date_range(self, date_range) -> None:
        """Validate date range format if provided."""
        if not date_range:
            return

        if not isinstance(date_range, (list)) or 2 <= len(date_range) < 1:
            raise ValueError(
                "Date range must be a list of 1-2 elements (past) or (past, future), "
                f"got {date_range}"
            )

        # Handle both single-element and two-element lists
        past = date_range[0] if len(date_range) > 0 else None
        future = date_range[1] if len(date_range) > 1 else None

        if past is None and future is None:
            return

        is_nearest = past.endswith(NEAREST_OPTIONS) if past else False

        # Validate past if provided
        if is_nearest:
            past_option = "np" if past.endswith("np") else "nd"
            past_int = int(past[:-2]) if past[:-2] else None
        else:
            past_option = past[-1] if past else None
            past_int = int(past[:-1]) if past else None

        # Validate past option and its integer value
        if (past_option not in DATE_RANGE_OPTIONS) or (
            past_option not in NEAREST_OPTIONS and past_int > 0
        ):
            raise ValueError(
                f"Invalid past '{past}'. Must end with "
                f"{DATE_RANGE_OPTIONS} and must be negative."
            )

        # Validate future if provided
        if future is None:
            return
        elif future.endswith(NEAREST_OPTIONS):
            raise ValueError(
                "Nearest need to be in this format, (<int><option>, ). "
                "Eg. ('6np',) or ('6nd',)"
            )
        else:
            future_option = future[-1] if future else None
            future_int = int(future[:-1]) if future else None

        # Validate future option and integer value
        if (future_option not in DATE_RANGE_OPTIONS) or (future_int < 0):
            raise ValueError(
                f"Invalid future '{future}'. Must end with "
                f"{DATE_RANGE_OPTIONS} and be positive."
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
