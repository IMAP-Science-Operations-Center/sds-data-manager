"""Simple utilities for reading dependency configurations."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from imap_data_access import VALID_INSTRUMENTS
from sqlalchemy import and_, desc, func
from sqlalchemy.orm import aliased

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.utils import (
    DependencyNode,
    UpstreamDependencyNode,
)

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
            - n - nearest
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
        date_range_options = ["p", "h", "d", "l", "n"]
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


class DependencyResolver():
    """Dependency lambda details

    Inputs:
        Source
        Data_type
        descriptor
        Start_time: yyyymmddhhmmss
        End_time: yyyymmddhhmmss

    Responsibilities:
        Lookup upstream dependencies
        Lookup downstream dependencies
        Find all relavant files for upstream dependencies
        Determining if it's a complete list.
            Scenarios causing imcompleteness:
                1. Missing files in the database.
                2. Due to event of anamoly. Eg. LOI or TCM or solar wind
                3. Due to repoint data delay or downlink delay.
                4. If required dependencies missing or job IN PROGRESS.

    Functionality:
    Look for all dependencies for given inputs and return all the available
    dependencies irrespective of if we have all the dependencies or not.
    Let user or caller code decide about it.

    Return type: dict
    Containing all available data for all identified upstream dependents within input date range. Eg.
    {
    status: 200,
    message: "success or missing but we still return all dependencies we found",
    data: {
        (Upstream details node): {
            missing_files: [(source, data_type, descriptor)],
            found_files: [list of files],
            }
        ...
    }

    """
    _config = DependencyConfigNew()

    def get_downstream_dependency_nodes(self, dependency_node: DependencyNode) -> list:
        """Get downstream dependency nodes for a given node.

        Parameters
        ----------
        dependency_node : DependencyNode
            The node for which to retrieve downstream dependencies.

        Returns
        -------
        list
            List of downstream DependencyNode for the given input DependencyNode.

        Examples
        --------
        >>> config.get_downstream_dependency_nodes(
            DependencyNode(source='swe', data_type='l1a', descriptor='all')
        )
        [DependencyNode(source='swe', data_type='l1b', descriptor='all'), ...]
        """
        if not isinstance(dependency_node, DependencyNode):
            raise ValueError(
                f"Input must be a DependencyNode instance, got {type(dependency_node)}"
            )

        downstream_dependency_nodes = []

        for downstream_node, upstream_dependencies in self._config.items():
            # get node from upstream_dependencies since upstream_nodes has
            # extra info such as required, kickoff_job, etc.
            upstream_nodes = [
                DependencyNode(*upstream_dependency[:3]) for upstream_dependency in upstream_dependencies
            ]
            if dependency_node in upstream_nodes:
                downstream_dependency_nodes.append(
                    DependencyNode(
                        source=downstream_node[0],
                        data_type=downstream_node[1],
                        descriptor=downstream_node[2],
                    )
                )

        return downstream_dependency_nodes

    def get_upstream_dependency(self, dependency_node: UpstreamDependencyNode):
        """Get upstream dependencies for a given downstream product.

        Parameters
        ----------
        dependency_node : UpstreamDependencyNode
            The downstream node for which to retrieve upstream dependencies.

        Returns
        -------
        dict
            Result dictionary with status, message, and data containing found files.

        Examples
        --------
        """
        upstream_deps = self._config.config.get(
            (
                dependency_node.source,
                dependency_node.data_type,
                dependency_node.descriptor,
            )
        )

        if not upstream_deps:
            return {
                "status": 404,
                "message": f"No upstream dependencies found for {dependency_node}",
                "data": {},
            }

        result_data = {}
        for upstream_dep in upstream_deps:
            upstream_node = UpstreamDependencyNode(
                source=upstream_dep[0],
                data_type=upstream_dep[1],
                descriptor=upstream_dep[2],
                start_date=dependency_node.start_date,
                end_date=dependency_node.end_date,
                reprocessing=upstream_dep[3] if len(upstream_dep) > 3 else True,
                repoint=upstream_dep[4] if len(upstream_dep) > 4 else True,
            )
            files = self._query_files(upstream_node)
            result_data[(upstream_node.source, upstream_node.data_type, upstream_node.descriptor)] = {
                "found_files": files,
                "required": upstream_dep[3] if len(upstream_dep) > 3 else True,
            }

        return {
            "status": 200,
            "message": "Upstream dependencies retrieved",
            "data": result_data,
        }

    def _query_files(
        self,
        dependency_node: UpstreamDependencyNode,
    ) -> list:
        """Query database for ScienceFile or AncillaryFile records.

        Parameters
        ----------
        dependency_node : UpstreamDependencyNode
            Node containing source, data_type, descriptor, and date range.

        Returns
        -------
        list
            List of file paths matching the query criteria.
        """
        with db.Session() as session:
            # Determine table based on data type
            if dependency_node.data_type == "ancillary":
                table = models.AncillaryFiles
            else:
                table = models.ScienceFiles

            type_specific_conditions = []
            if dependency_node.data_type == "ancillary":
                # Ancillary files typically use descriptor for file identification
                type_specific_conditions = [
                    table.descriptor == dependency_node.descriptor,
                ]
            else:
                # Science files use data_level (data_type) and descriptor
                type_specific_conditions = [
                    table.data_level == dependency_node.data_type,
                    table.descriptor == dependency_node.descriptor,
                ]

            filter_conditions = [
                table.instrument == dependency_node.source,
                *type_specific_conditions,
                table.start_date >= dependency_node.start_date,
                table.start_date <= dependency_node.end_date,
            ]

            # Get latest version per start_date
            max_version_query = (
                session.query(
                    table.start_date, func.max(table.version).label("latest_version")
                )
                .filter(*filter_conditions)
                .group_by(table.start_date)
                .subquery()
            )

            # Query records with latest versions
            records = (
                session.query(table)
                .join(
                    max_version_query,
                    (table.start_date == max_version_query.c.start_date)
                    & (table.version == max_version_query.c.latest_version),
                )
                .filter(*filter_conditions)
                .all()
            )

            # For ancillary files, return only the latest
            if dependency_node.data_type == "ancillary" and records:
                return [records[-1].file_path]

            return [record.file_path for record in records]

    def get_science_files(
        self,
        source: str,
        data_type: str,
        descriptor: str,
        start_date: datetime,
        end_date: datetime,
    ) -> list:
        """Get science files for a given product.

        Parameters
        ----------
        source : str
            Instrument source name.
        data_type : str
            Data level (e.g., 'l1a', 'l1b', 'l2').
        descriptor : str
            Data descriptor.
        start_date : datetime
            Start date for file query.
        end_date : datetime
            End date for file query.

        Returns
        -------
        list
            List of file paths matching the criteria.
        """
        with db.Session() as session:
            table = models.ScienceFiles

            filter_conditions = [
                table.instrument == source,
                table.data_level == data_type,
                table.descriptor == descriptor,
                table.start_date >= start_date,
                table.start_date <= end_date,
            ]

            max_version_query = (
                session.query(
                    table.start_date, func.max(table.version).label("latest_version")
                )
                .filter(*filter_conditions)
                .group_by(table.start_date)
                .subquery()
            )

            records = (
                session.query(table)
                .join(
                    max_version_query,
                    (table.start_date == max_version_query.c.start_date)
                    & (table.version == max_version_query.c.latest_version),
                )
                .filter(*filter_conditions)
                .all()
            )

            return [record.file_path for record in records]

    def get_ancillary_files(
        self,
        source: str,
        descriptor: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> list:
        """Get ancillary files for a given product.

        Parameters
        ----------
        source : str
            Instrument source name.
        descriptor : str
            Ancillary file descriptor.
        start_date : datetime, optional
            Start date for file query. If None, no lower bound.
        end_date : datetime, optional
            End date for file query. If None, uses latest file.

        Returns
        -------
        list
            List of latest ancillary file paths matching the criteria.
        """
        with db.Session() as session:
            table = models.AncillaryFiles

            filter_conditions = [
                table.instrument == source,
                table.descriptor == descriptor,
            ]

            if start_date:
                filter_conditions.append(table.start_date >= start_date)
            if end_date:
                filter_conditions.append(table.start_date <= end_date)

            # Get latest version per start_date
            max_version_query = (
                session.query(
                    table.start_date, func.max(table.version).label("latest_version")
                )
                .filter(*filter_conditions)
                .group_by(table.start_date)
                .subquery()
            )

            records = (
                session.query(table)
                .join(
                    max_version_query,
                    (table.start_date == max_version_query.c.start_date)
                    & (table.version == max_version_query.c.latest_version),
                )
                .filter(*filter_conditions)
                .all()
            )

            # Return only the latest ancillary file
            if records:
                return [records[-1].file_path]
            return []

    def get_spice_files(
        self,
        descriptor: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> list:
        """Get SPICE kernel files.

        Parameters
        ----------
        descriptor : str
            SPICE kernel descriptor (e.g., 'leapseconds', 'predict').
        start_date : datetime, optional
            Start date for file query.
        end_date : datetime, optional
            End date for file query.

        Returns
        -------
        list
            List of SPICE kernel file paths matching the criteria.
        """
        with db.Session() as session:
            table = models.SPICEFiles

            filter_conditions = [table.descriptor == descriptor]

            if start_date:
                filter_conditions.append(table.start_date >= start_date)
            if end_date:
                filter_conditions.append(table.start_date <= end_date)

            records = session.query(table).filter(*filter_conditions).all()

            return [record.file_path for record in records]
