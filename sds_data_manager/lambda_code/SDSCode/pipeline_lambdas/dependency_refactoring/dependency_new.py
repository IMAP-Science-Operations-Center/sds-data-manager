"""Simple utilities for reading dependency configurations."""

import logging
import os
from pathlib import Path

import requests
import yaml
from imap_data_access import VALID_INSTRUMENTS
from sqlalchemy import and_, func, or_

from ...api_lambdas import upload_api
from ...database import database as db
from ...database import models
from .. import REPOINT_DEPENDENT_INSTRUMENTS
from ..dependency import DataType
from .types import DependencyNode, ProcessingJobNode, format_upstream_node_input

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
            :class:`~.types.DependencyNode` upstream dependency objects.
        """
        return self._config

    def _load_all_dependencies(
        self,
    ) -> dict[tuple[str, str, str], list[DependencyNode]]:
        """Load all instrument YAML dependency files and unified dependency.

        Returns a dictionary where each key is a parent node
        (source, data_type, descriptor) representing a downstream product,
        and each value is a list of upstream :class:`~.types.DependencyNode`
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

    def get_upstream_dependency(self, session, input_upstream_node: ProcessingJobNode):
        """Get upstream dependencies for a given upstream node.

        ProcessingJobNode contains required Inputs:
            Source
            Data_type
            descriptor
            time_span: TimeRange with start_time and end_time

        Responsibilities:
            - Lookup upstream dependencies, using the configuration in _config
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
        input_upstream_node : ProcessingJobNode
            The input node with source, data_type, descriptor, and time_span.

        Returns
        -------
        dict
            A dictionary with status code, message, and data.
            The data contains serialized upstream dependencies for
            job submission.
        """
        upstream_deps = self._config.get(
            (
                input_upstream_node.source,
                input_upstream_node.data_type,
                input_upstream_node.descriptor,
            )
        )

        if not upstream_deps:
            return {
                "status": 404,
                "message": f"No upstream dependencies found for {input_upstream_node}",
                "data": {},
            }

        return {"status": 200, "message": "Success", "data": {}}

    def get_files(
        self,
        session: db.Session,
        dependency_node: DependencyNode,
        scope: ProcessingJobNode,
    ) -> list:
        """Query ScienceFiles or AncillaryFiles for one upstream dependency_node.

        Ported from ``get_files`` in
        ``pipeline_lambdas/dependency.py``. For each ``start_date``
        only the row with the latest ``version`` is returned. For
        ancillary edges the result is further reduced to the
        single row with the latest ``start_date``.

        Parameters
        ----------
        session : db.Session
            Open database session supplied by the caller.
        dependency_node : DependencyNode
            Upstream dependency_node identifying which instrument, data type,
            and descriptor to query for.
        scope : ProcessingJobNode
            Job-scope node supplying time_span (start_time, end_time)
            and (optionally) pointing filters.

        Returns
        -------
        list
            Matching ``models.ScienceFiles`` or
            ``models.AncillaryFiles`` rows.
        """
        type_specific_conditions = []
        if dependency_node.data_type == DataType.ANCILLARY:
            table = models.AncillaryFiles
            # Date-range overlap: ancillary file's [start, end]
            # window overlaps the requested [start, end] window.
            type_specific_conditions.append(
                and_(
                    table.start_date <= scope.time_span.end_time,
                    or_(
                        table.end_date >= scope.time_span.start_time,
                        table.end_date.is_(None),
                    ),
                )
            )
        else:
            table = models.ScienceFiles
            type_specific_conditions.append(
                table.data_level == dependency_node.data_type
            )
            # Repoint-dependent instruments: filter by repoint
            # rather than date. Date filtering would incorrectly
            # exclude files when the caller's date range doesn't
            # match the target repoint's pointing dates.
            if (
                scope.time_span.pointing_number_start is not None
                and dependency_node.source in REPOINT_DEPENDENT_INSTRUMENTS
            ):
                type_specific_conditions.append(
                    table.repointing == scope.time_span.pointing_number_start
                )
            else:
                type_specific_conditions.append(
                    and_(
                        table.start_date >= scope.time_span.start_time,
                        table.start_date <= scope.time_span.end_time,
                    )
                )

        filter_conditions = [
            table.instrument == dependency_node.source,
            table.descriptor == dependency_node.descriptor,
            *type_specific_conditions,
        ]
        # Latest version per start_date.
        max_version_query = (
            session.query(
                table.start_date,
                func.max(table.version).label("latest_version"),
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
        if dependency_node.data_type == DataType.ANCILLARY:
            records = sorted(
                records,
                key=lambda x: x.start_date,
                reverse=True,
            )[0:1]
        return records


def upload_dependency_file(dependency_file_path: Path, serialized_dependencies: str):
    """Upload a JSON file containing a job's dependencies to S3.

    Parameters
    ----------
    dependency_file_path : Path
        The dependency JSON file to upload.
    serialized_dependencies : str
        The serialized upstream dependencies to upload.
    """
    # Check if the file already exists
    if os.path.isfile(dependency_file_path):
        raise KeyError(
            f"{dependency_file_path} already exists, cannot create JSON file."
        )
    # call the upload API handler directly
    signed_url = upload_api.lambda_handler(
        {
            "pathParameters": {"proxy": dependency_file_path.as_posix()},
            "requestContext": {
                "authorizer": {"lambda": {"scope": "write", "apiKey": "batch-starter"}}
            },
        },
        None,
    )
    if signed_url["statusCode"] == 409:
        logger.info(
            f"Dependency file already exists in S3: {dependency_file_path}. Reusing"
            f"file."
        )
        return {"statusCode": 200, "body": signed_url["body"]}
    elif signed_url["statusCode"] != 200:
        logger.error(
            f"Failed to get S3 pre-signed URL for file: {dependency_file_path}. "
            f"As a result, failed to kick off job. "
            f"Error message: {signed_url['body']}, "
            f"with status code: {signed_url['statusCode']}."
        )
        return None
    try:
        response = requests.put(
            signed_url["body"].strip('"'),
            data=serialized_dependencies,
            headers={"Content-Type": "application/json"},
            timeout=60.0,
        )
        logger.info(
            f"Dependency file uploaded successfully to s3 with status code: "
            f"{response.status_code}"
        )
        return response
    except Exception as e:
        logger.error(
            f"Unexpected error during cadence file upload: {e}. "
            f"Dependency file upload failed and the job did not get kicked off."
        )
        return None
