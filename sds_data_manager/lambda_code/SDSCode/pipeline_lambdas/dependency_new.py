"""Simple utilities for reading dependency configurations."""

import json
import logging
from datetime import datetime, timedelta, timezone
from os.path import basename
from pathlib import Path
from typing import Optional

import imap_data_access
import yaml
from imap_data_access import VALID_INSTRUMENTS, processing_input
from imap_data_access.processing_input import ProcessingInputCollection
from sqlalchemy import and_, desc, func
from sqlalchemy.orm import aliased

from sds_data_manager.lambda_code.SDSCode.api_lambdas import spice_metakernel_api
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.utils import (
    DependencyNode,
    UpstreamDependencyNode,
)

from .dependency import DataSource, DataType

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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


class DependencyResolver:
    """Resolve upstream and downstream dependencies for data products.

    Manages dependency configuration and file lookups for IMAP data products.

    Inputs:
        Source
        Data_type
        descriptor
        Start_time: yyyymmddhhmmss
        End_time: yyyymmddhhmmss

    Responsibilities:
        - Lookup upstream dependencies
        - Lookup downstream dependencies
        - Find all relevant files for upstream dependencies
        - Determine if it's a complete list
            Scenarios causing incompleteness:
                1. Missing files in the database.
                2. Due to anomaly (e.g., LOI, TCM, solar wind).
                3. Due to repoint data delay or downlink delay.
                4. If required dependencies missing or job IN PROGRESS.

    Functionality:
        Look for all dependencies for given inputs and return all the available
        dependencies regardless of completeness. Let caller code decide about it.

    Return type:
        dict with status (200 or 4xx), message, and data containing found files
        for each upstream dependency node.
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
                DependencyNode(*upstream_dependency[:3])
                for upstream_dependency in upstream_dependencies
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

    def get_upstream_dependency(  # noqa: PLR0912
        self, session, upstream_node: UpstreamDependencyNode
    ):
        """Get upstream dependencies for a given downstream product.

        Parameters
        ----------
        session : db.Session
            Database session for file queries.
        upstream_node : UpstreamDependencyNode
            The upstream node for which to retrieve dependencies.

        Returns
        -------
        dict
            Result dictionary with status (200/404), message, and file data.
        """
        upstream_deps = self._config.config.get(
            (
                upstream_node.source,
                upstream_node.data_type,
                upstream_node.descriptor,
            )
        )

        if not upstream_deps:
            return {
                "status": 404,
                "message": f"No upstream dependencies found for {upstream_node}",
                "data": {},
            }

        # Status, message, data.
        result_data = {}
        data_collection = ProcessingInputCollection()
        # Check for SPICE dependencies first.
        # -----------------------------
        # Check for SPICE dependencies
        # -----------------------------
        # If spin is a dependency, query spin table for given date range
        has_spin_dep = any(dep["data_source"] == "spin" for dep in upstream_deps)
        if has_spin_dep:
            spin_records = self.get_spin_files(
                session, upstream_node.start_date, upstream_node.end_date
            )
            if not spin_records:
                result_data[("spin", "spin", "historical")] = {
                    "found_files": [],
                }
            else:
                spin_files = [basename(record.file_path) for record in spin_records]
                logger.info(f"Found spin files: {spin_files}. Adding to collection.")
                data_collection.add(processing_input.SpinInput(*spin_files))

        # If repoint is a dependency, query s3 for latest repoint file
        has_repoint_dep = any(dep["data_source"] == "repoint" for dep in upstream_deps)
        if has_repoint_dep:
            latest_repoint_file = self.get_latest_repoint_file(upstream_node.end_date)
            if not latest_repoint_file:
                result_data[("repoint", "repoint", "historical")] = {
                    "found_files": [],
                }
            else:
                logger.info(
                    f"Found repoint file: {latest_repoint_file}. Adding to collection."
                )
                data_collection.add(processing_input.RepointInput(latest_repoint_file))

        has_kernel_dep = any(
            dep["data_source"] != "spin"
            and dep["data_source"] != "repoint"
            and dep["data_type"] == "spice"
            for dep in upstream_deps
        )
        if has_kernel_dep:
            kernels_records = self.get_spice_files(upstream_node, upstream_deps)
            if not kernels_records:
                result_data[("all", "spice", "best")] = {
                    "found_files": [],
                }

            else:
                kernel_files = [basename(record) for record in kernels_records]
                logger.info(
                    f"Found kernel files: {kernel_files}. Adding to collection."
                )
                data_collection.add(processing_input.SpiceKernelInput(*kernel_files))

        # Ancillary dependencies
        ancillary_deps = [
            dep
            for dep in upstream_deps
            if dep["data_source"] in VALID_INSTRUMENTS
            and dep["data_type"] == "ancillary"
        ]
        for ancillary_dependency in ancillary_deps:
            ancillary_files = self.get_ancillary_files(
                source=ancillary_dependency["data_source"],
                descriptor=ancillary_dependency["descriptor"],
                start_date=upstream_node.start_date,
                end_date=upstream_node.end_date,
            )
            if not ancillary_files:
                result_data[
                    (
                        ancillary_dependency["data_source"],
                        ancillary_dependency["data_type"],
                        ancillary_dependency["descriptor"],
                    )
                ] = {
                    "found_files": [],
                }
            else:
                logger.info(
                    f"Found ancillary files: {ancillary_files}. Adding to collection."
                )
                data_collection.add(processing_input.AncillaryInput(*ancillary_files))

        # Science dependencies
        science_deps = [
            dep
            for dep in upstream_deps
            if dep["data_source"] in VALID_INSTRUMENTS
            and dep["data_type"] != "ancillary"
        ]
        for science_dependency in science_deps:
            science_files = self.get_science_files(upstream_node, science_dependency)
            if not science_files:
                result_data[
                    (
                        science_dependency["data_source"],
                        science_dependency["data_type"],
                        science_dependency["descriptor"],
                    )
                ] = {
                    "found_files": [],
                }
            else:
                logger.info(
                    f"Found science files: {science_files}. Adding to collection."
                )
                data_collection.add(processing_input.ScienceInput(*science_files))

        return {
            "status": 200,
            "message": "Upstream dependencies retrieved",
            "data": result_data,
        }

    def get_science_files(
        self, upstream_node: UpstreamDependencyNode, upstream_dependency: tuple
    ) -> list:
        """Get science files for a given product.

        Parameters
        ----------
        upstream_node : UpstreamDependencyNode
            Node containing source, data_type, descriptor, and actual date range,
            reprocesing flag or repoint id.
        upstream_dependency : tuple
            Tuple containing upstream dependency details from config, including
            source, data_type, descriptor, required, kickoff_job, and optional
            date range to look up.

        Returns
        -------
        list
            List of file paths matching the criteria.
        """
        pass

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
        pass

    def get_spin_files(
        self,
        session,
        start_date: datetime,
        end_date: datetime,
    ) -> list:
        """Get spin input.

        Query the spin table for the given date range and get latest version.

        Parameters
        ----------
        session : orm session
            Database session.
        start_date : datetime
            Start date to find dependent files with.
        end_date : datetime
            End date to find dependent files with.

        Returns
        -------
        list
            List of SpinFiles records with file_path, start_date, end_date, version.
        """
        spin = aliased(models.SpinFiles)

        # Define the row_number() window function
        row_number = (
            func.row_number()
            .over(
                partition_by=(spin.start_date, spin.end_date),
                order_by=desc(spin.version),
            )
            .label("row_num")
        )

        # Build the subquery with row numbers
        subquery = (
            session.query(
                spin.file_path, spin.start_date, spin.end_date, spin.version, row_number
            )
            .filter(
                and_(
                    spin.start_date <= end_date,
                    spin.end_date >= start_date,
                )
            )
            .subquery()
        )

        # Outer query to select only latest version per start/end date
        records = (
            session.query(
                subquery.c.file_path,
                subquery.c.start_date,
                subquery.c.end_date,
                subquery.c.version,
            )
            .filter(subquery.c.row_num == 1)
            .all()
        )

        return records

    def get_latest_repoint_file(self, end_date: datetime) -> Optional[str]:
        """Get latest repoint file.

        Query for the latest repoint file for given end_date.

        Parameters
        ----------
        end_date : datetime
            End date to find dependent files with.

        Returns
        -------
        str
            Latest repoint file name.
        """
        with db.Session() as session:
            latest_repoint_file = (
                session.query(models.RepointFiles)
                .order_by(desc(models.RepointFiles.file_path))
                .first()
            )

        if not latest_repoint_file:
            raise ValueError("No Repoint file found in the database.")

        if latest_repoint_file.end_date < end_date:
            logger.info(
                f"Latest repoint file end date {latest_repoint_file.end_date} "
                f"is before input end date {end_date}"
            )
            return None

        return basename(latest_repoint_file.file_path)

    def get_spice_files(
        self, upstream_node: UpstreamDependencyNode, upstream_deps: list
    ) -> list:
        """Retrieve SPICE kernel files for given time range and kernel types."""
        combined_kernel_sources = self.combine_kernel_sources(upstream_deps)

        # convert start_date and end_date in seconds after j2000.
        # TODO: remove this once Bryan changes takes in 'yyyymmdd' format
        def yyyymmdd_to_seconds_since_j2000(date_str: str, add_24_hrs=False) -> float:
            # Parse input date string
            dt = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
            if add_24_hrs:
                dt += timedelta(hours=24)
            # Define J2000 epoch: 2000-01-01T12:00:00 UTC
            j2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

            # Compute seconds difference
            delta = dt - j2000
            return delta.total_seconds()

        start_time = yyyymmdd_to_seconds_since_j2000(
            upstream_node.start_date.strftime("%Y%m%d")
        )
        # TODO revisit setting end_time after SIT-4. Should be handled upstream
        if (
            upstream_node.end_date == upstream_node.start_date
            or upstream_node.repoint is not None
        ):
            add_24_hrs = True
        else:
            add_24_hrs = False
        end_time = yyyymmdd_to_seconds_since_j2000(
            upstream_node.end_date.strftime("%Y%m%d"), add_24_hrs
        )
        metakernel_response = spice_metakernel_api.lambda_handler(
            {
                "queryStringParameters": {
                    "start_time": start_time,
                    "end_time": end_time,
                    "list_files": "True",
                    "file_types": combined_kernel_sources,
                    # TODO: revisit this after SIT-4
                    # "require_coverage": "True",
                }
            },
            None,
        )
        if metakernel_response["statusCode"] != 200:
            logger.error(
                f"Metakernel lambda raised error: {metakernel_response['body']}"
            )
            return None
        metakernel_files = json.loads(metakernel_response["body"])
        # If number of kernels returned doesn't match the number of file types
        # requested
        has_all_kernels = self.check_requested_kernels(
            combined_kernel_sources, metakernel_files
        )
        if not has_all_kernels:
            return None

        logger.info(
            f"Found metakernel files: {metakernel_files}. Adding to collection."
        )
        return metakernel_files

    def combine_kernel_sources(self, dependency: dict) -> str:
        """Combine kernel sources.

        Combine the kernel sources to form a single string separated by commas.
        This is used in metakernel API calls to get kernels in order list.

        Parameters
        ----------
        dependency : dict
            Dependency dictionary containing the data source and data type.

        Returns
        -------
        str
            Combined kernel sources separated by commans. Eg.
            "attitude_history,attitude_predict,..."
        """
        file_types = []
        for dep in dependency:
            if dep["data_source"] in spice_metakernel_api.KernelCollection().file_types:
                file_types.append(dep["data_source"])
        return ",".join(file_types)

    def check_requested_kernels(self, combined_kernel_sources, metakernel_files):
        """Check if all requested kernels are present in the metakernel files.

        We need to ensure that the returned list of metakernel files includes
        all requested kernels, especially for ephemeris kernels. The API can
        return the "best" ephemeris kernels, which can include both historical
        and predicted kernels depending on the input time range. If the user
        specifically requests only historical ephemeris kernels, we must verify
        that only historical files are returned. Otherwise, both historical
        and predicted kernels are acceptable.

        Additionally, the API can return multiple kernels for the same source
        if the files cover specific date ranges. Because of this, we must
        check that all requested sources are present in the returned
        metakernel files, rather than performing a direct one-to-one
        comparison. Each source may correspond to multiple kernel files.

        Parameters
        ----------
        combined_kernel_sources : str
            Comma-separated string of requested kernel sources.
        metakernel_files : list
            List of metakernel files found.

        Returns
        -------
        bool
            True if all requested kernels are found, False otherwise.
        """
        requested_kernels = set(combined_kernel_sources.split(","))
        expected_ephemeris = set(
            [kernel for kernel in requested_kernels if "ephemeris_" in kernel]
        )
        expected_other_kernels = set(
            [kernel for kernel in requested_kernels if "ephemeris_" not in kernel]
        )

        ephemeris_found = set()
        other_kernels_found = set()

        for file in metakernel_files:
            file_obj = imap_data_access.SPICEFilePath(file)
            # Extract the kernel type from the file name
            kernel_type = file_obj.spice_metadata["type"]
            if "ephemeris_" in kernel_type:
                ephemeris_found.add(kernel_type)
            else:
                other_kernels_found.add(kernel_type)

        # Check if all other requested kernels are found
        if expected_other_kernels != other_kernels_found:
            logger.error(
                f"Non-ephemeris kernels {expected_other_kernels} not found in "
                f"metakernel files {other_kernels_found}"
            )
            return False

        # If no ephemeris kernels are requested, we can return True.
        if not expected_ephemeris:
            return True

        # If only historical ephemeris kernel is requested, check that it
        # is found.
        if (
            len(expected_ephemeris) == 1
            and next(iter(expected_ephemeris)) == "ephemeris_reconstructed"
            and "ephemeris_reconstructed" in ephemeris_found
        ):
            return True

        # If 'best' ephemeris kernel is requested, check that at least one
        # of the kernels is found in the metakernel files.
        if (
            len(expected_ephemeris) > 1
            and any("ephemeris_" in kernel for kernel in expected_ephemeris)
            and any("ephemeris_" in kernel for kernel in ephemeris_found)
        ):
            return True

        logger.error(
            f"Requested ephemeris kernels: {expected_ephemeris}, "
            f"found in metakernel files: {ephemeris_found}"
            f"\nRequested other kernels: {expected_other_kernels}, "
            f"found in metakernel files: {other_kernels_found}"
        )
        return False
