"""Dependency tracking module."""

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from os.path import basename
from pathlib import Path
from typing import Optional

import boto3
import imap_data_access
from imap_data_access import processing_input
from imap_data_access.processing_input import ProcessingInputCollection
from sqlalchemy import and_, func, or_

from ..api_lambdas import spice_metakernel_api
from ..database import database as db
from ..database import models

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass
class DataSource:
    """Valid data sources for dependency tracking.

    Valid data sources include valid instruments names
    from imap_data_access and other data sources related to SPICE.
    """

    @property
    def valid_source(self) -> list[str]:
        """Add data sources.

        Returns
        -------
        list[str]
            list of valid data sources.
        """
        # TODO: import this from imap_data_access once it's defined
        # or transition this class to imap_data_access
        return [
            "spin",
            "repoint",
            *spice_metakernel_api.KernelCollection().file_types,
            *imap_data_access.VALID_INSTRUMENTS,
        ]


def valid_science(data_level) -> bool:
    """Check if data_level is a valid data level.

    Returns
    -------
    bool
        True if the data_level is in VALID_DATALEVELS.
    """
    return data_level in [*imap_data_access.VALID_DATALEVELS]


@dataclass
class DataType:
    """Valid data types for dependency tracking.

    Valid data types include valid data levels from imap_data_access
    and other data types related to SPICE and ancillary data.
    """

    # TODO: transition these class to imap_data_access once it's defined.
    SPICE: str = "spice"
    SPIN: str = "spin"
    REPOINT: str = "repoint"
    ANCILLARY: str = "ancillary"

    @property
    def valid_type(self) -> list[str]:
        """Add data types.

        Returns
        -------
        list[str]
            list of valid data types.
        """
        return [
            self.SPICE,
            self.ANCILLARY,
            self.SPIN,
            self.REPOINT,
            *imap_data_access.VALID_DATALEVELS,
        ]


@dataclass
class DataDescriptor:
    """Valid data descriptors for dependency tracking.

    Every IMAP science data product has its data descriptor.
    TODO: Include all valid science data descriptors from
    imap_data_access once it's defined.

    Here, we add descriptors related to SPICE and other data types.
    Valid data descriptors for SPICE and other data types are:
        1. predict - Predicted data
        2. historical - Historical data
        3. reconstruct - Reconstructed data
        4. nominal - Nominal data
        5. best - BEST will be used to decide if metakernels
                  will include predict or reconstruct kernels
                  if historical kernels are not available.
    """

    # TODO: transition these class to imap_data_access once it's defined
    PREDICT: str = "predict"
    HISTORICAL: str = "historical"
    RECONSTRUCT: str = "reconstruct"
    NOMINAL: str = "nominal"
    BEST: str = "best"

    @property
    def valid_descriptor(self) -> list[str]:
        """Add data descriptors.

        Returns
        -------
        list[str]
            list of valid data descriptors.
        """
        return [
            self.PREDICT,
            self.HISTORICAL,
            self.RECONSTRUCT,
            self.NOMINAL,
            self.BEST,
        ]


@dataclass
class Relationship:
    """Valid data relationships for dependency tracking.

    Valid data relationships are:
        1. HARD - the data is required for the pipeline to run
        2. SOFT_TRIGGER - the data is optional for the pipeline to run. A new file will
            trigger processing.
        3. SOFT_NO_TRIGGER - the data is optional for the pipeline to run. A new file
            will not trigger processing.
    """

    # TODO: transition these class to imap_data_access once it's defined
    HARD: str = "HARD"
    HARD_NO_TRIGGER: str = "HARD_NO_TRIGGER"
    SOFT_TRIGGER: str = "SOFT_TRIGGER"
    SOFT_NO_TRIGGER: str = "SOFT_NO_TRIGGER"

    @property
    def valid_relationship(self) -> list[str]:
        """Add data relationships.

        Returns
        -------
        list[str]
            list of valid data relationships.
        """
        return [
            self.HARD,
            self.HARD_NO_TRIGGER,
            self.SOFT_TRIGGER,
            self.SOFT_NO_TRIGGER,
        ]


@dataclass
class DependencyType:
    """Valid data dependency type for dependency tracking.

    Valid data dependency types are:
        1. UPSTREAM - Processed product to start current product's process
        2. DOWNSTREAM - future file that needs current file to start its process
    """

    # TODO: transition these class to imap_data_access once it's defined
    UPSTREAM: str = "UPSTREAM"
    DOWNSTREAM: str = "DOWNSTREAM"

    @property
    def valid_dependency_type(self) -> list[str]:
        """Add data dependency types.

        Returns
        -------
        list[str]
            list of valid data dependency types.
        """
        return [self.UPSTREAM, self.DOWNSTREAM]


class DependencyConfig:
    """Dependency configuration for IMAP Products.

    We can keep track of dependencies by tracking nodes in a graph. Each node
    represents a data product and the edges represent the dependencies between
    them. There is an upstream/downstream relationship between nodes. A node
    can be any data product, from a science file (instrument, data level, descriptor),
    a SPICE file, or an ancillary file.

    For example, dependency can be accessed like this:
        dependencies["HARD"]["DOWNSTREAM"][('hit', 'l0', 'raw')]
        where ('hit', 'l0', 'raw') is the parent node.

        Example output of above call:
            [('hit', 'l1a', 'all'), ('hit', 'l1b', 'hk')]
    """

    def __init__(self):
        """Read dependency configuration from dependency_config.csv."""
        self.data_source = DataSource()
        self.data_type = DataType()
        self.data_descriptor = DataDescriptor()
        self.relationship = Relationship()
        self.dependency_type = DependencyType()
        self.dependencies = self._load_dependencies()

    def _load_dependencies(self) -> dict:
        """Load dependencies from dependency_config.csv.

        Returns
        -------
        dict
            dictionary of dependencies.
        """
        dependencies = {
            hard_soft: {
                up_down: defaultdict(list)
                for up_down in self.dependency_type.valid_dependency_type
            }
            for hard_soft in self.relationship.valid_relationship
        }

        with open(Path(__file__).parent / "dependency_config.csv") as f:
            for line in f:
                # NOTE: remove extra ',,,,,,,' if you edited the csv file in excel.
                if len(line) <= 1 or line.startswith("#"):
                    # Skip empty lines and comments
                    continue
                contents = line.strip().replace(", ", ",").split(",")
                header = [
                    "primary_source",
                    "primary_data_type",
                    "primary_descriptor",
                    "dependent_source",
                    "dependent_data_type",
                    "dependent_descriptor",
                    "relationship",
                    "dependency_type",
                ]

                if len(contents) != 8:
                    raise ValueError(
                        f"Each dependency should have {header}\nCurrent line: {line}"
                    )

                # data_source, data_type, descriptor
                parent_node = tuple(contents[:3])
                child_node = tuple(contents[3:6])

                try:
                    self._validate_node(parent_node)
                    self._validate_node(child_node)
                except ValueError as e:
                    raise ValueError(f"Node validation failed with '{e}'") from e

                hard_soft = contents[6]
                # Downstream direction
                dependencies[hard_soft][self.dependency_type.DOWNSTREAM][
                    parent_node
                ].append(child_node)
                # Upstream direction (flip parent/child)
                dependencies[hard_soft][self.dependency_type.UPSTREAM][
                    child_node
                ].append(parent_node)

        return dependencies

    def _validate_node(self, node: tuple) -> bool:
        """Validate node.

        Parameters
        ----------
        node : tuple
            Node to validate.
        """
        if len(node) != 3:
            raise ValueError("Missing data source, data type, or descriptor")
        if node[0] not in self.data_source.valid_source:
            raise ValueError(
                f"Invalid data source: {node[0]}. "
                f"Valid data sources: {self.data_source.valid_source}"
            )
        if node[1] not in self.data_type.valid_type:
            raise ValueError(
                f"Invalid data type: {node[1]}. "
                f"Valid data types: {self.data_type.valid_type}"
            )
        # TODO: Add descriptor validation once we define all data product's
        # data descriptor.

    def kickoff_pipeline_jobs(self) -> list:
        """Return all the jobs that kick off each instrument pipeline.

        These are nodes that are downstream from a node with the data_level equal to
        "l0" and the descriptor equal to "raw".

        Returns
        -------
        list
            List of dictionaries containing the data source, data type, and descriptor
            of the jobs that kick off each instrument pipeline.
        """
        kick_off_jobs = []
        for relationship in [self.relationship.HARD, self.relationship.SOFT_TRIGGER]:
            # Get all the downstream dependencies for the l0 raw data
            dependencies = self.dependencies[relationship]["DOWNSTREAM"]
            for parent_node, child_node in dependencies.items():
                # If the parent dependency is l0 raw, add the child dependencies to the
                # kick_off_jobs list.
                if parent_node[1] == "l0" and parent_node[2] == "raw":
                    kick_off_jobs.extend(
                        [
                            {
                                "data_source": node[0],
                                "data_type": node[1],
                                "descriptor": node[2],
                            }
                            for node in child_node
                        ]
                    )
        return kick_off_jobs


def get_dependencies(node, dependency_type, relationship):
    """Lookup the dependencies for the given ``node``.

    A ``node`` is an identifier of the data product, which can be an
    (data_source, data_type, descriptor) tuple, science file identifiers,
    or SPICE file identifiers, or ancillary data file identifiers.

    Parameters
    ----------
    node : tuple
        Quantities that uniquely identify a data product.
    dependency_type : str
        Whether it's UPSTREAM or DOWNSTREAM dependency.
    relationship : str
        Whether it's HARD, SOFT_TRIGGER, or SOFT_NO_TRIGGER dependency.
        HARD means data is required for pipeline and SOFT_TRIGGER and SOFT_NO_TRIGGER
        means data is optional for pipeline. A SOFT_TRIGGER file will trigger processing
        and reprocessing. If "ALL" is provided, dependencies for all valid relationships
        (HARD, SOFT_TRIGGER, SOFT_NO_TRIGGER) will be returned.

    Returns
    -------
    dependencies : list
        List of dictionary containing the dependency information.
    """
    # Load the dependencies
    dependency_config = DependencyConfig()

    relationships = (
        Relationship().valid_relationship if relationship == "ALL" else [relationship]
    )

    dependencies = []
    for rel in relationships:
        deps = dependency_config.dependencies[rel][dependency_type].get(node, [])
        # Add keys for a dict-like representation
        dependencies.extend(
            [
                {
                    "data_source": dep[0],
                    "data_type": dep[1],
                    "descriptor": dep[2],
                    "relationship": rel,
                }
                for dep in deps
            ]
        )
    return dependencies


def primary_science_dep(query_params: dict, dependency: dict) -> bool:
    """Check if the dependency is a primary science dependency.

    A primary science dependency exists when both the upstream and downstream
    dependencies have the same data source and both are valid science data types. They
    can have different descriptors, e.g., "sci" and "raw".

    Parameters
    ----------
    query_params : dict
        Query parameters
    dependency : dict
       Upstream or downstream dependency from the query.

    Returns
    -------
    bool
        True if dependency is a primary science dependency, False otherwise.

    Examples
    --------
    swe_l1b_sci is a primary science dependency of swe_l1a_sci.
    mag_l1c_norm-mago is a primary science dependency of mag_l1b_burst-mago.
    """
    return (
        query_params["data_source"] == dependency["data_source"]
        and valid_science(dependency["data_type"])
        and valid_science(query_params["data_type"])
    )


def combine_kernel_sources(dependency: dict) -> str:
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


def get_spin_files(
    session,
    start_date: datetime,
    end_date: datetime,
) -> list:
    """Get spin input.

    Query the spin table for the given date range.

    Parameters
    ----------
    session : orm session
        Database session.
    spice_denpendencies : list
        Dependency list containing dictionary of SPICE dependencies.
    start_date : datetime
        Start date to find dependent files with.
    end_date : datetime
        End date to find dependent files with.

    Returns
    -------
    list
        List of spin files.
    """
    # Query the spin table for the given date range
    records = (
        session.query(models.SpinTable)
        .filter(
            and_(
                models.SpinTable.start_date <= end_date,
                models.SpinTable.end_date >= start_date,
            )
        )
        .all()
    )

    spin_files = [basename(record.file_path) for record in records]
    return spin_files


def get_latest_repoint_file(end_date: datetime) -> Optional[str]:
    """Get latest repoint file.

    Query S3 bucket for the latest repoint file.

    Parameters
    ----------
    end_date : datetime
        End date to find dependent files with.

    Returns
    -------
    str
        Latest repoint file name.
    """
    bucket_name = os.getenv("S3_BUCKET")
    prefix = "imap/spice/repoint/"

    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)

    repoint_files = []

    for page in pages:
        for obj in page.get("Contents", []):
            filename = obj["Key"]
            file_obj = processing_input.SPICEFilePath(filename)
            repoint_files.append(
                (
                    file_obj.spice_metadata["end_date"],
                    file_obj.spice_metadata["version"],
                    filename,
                )
            )

    if not repoint_files:
        return None

    # Sort by end_date and version
    latest = sorted(repoint_files, key=lambda x: (x[0], x[1]))[-1]
    latest_file_date = latest[0]

    # Check that input end is within latest repoint file end date
    if latest_file_date < end_date:
        logger.info(
            f"Latest repoint file end date {latest_file_date} "
            f"is before input end date {end_date}"
        )
        return None

    # Otherwise, return the latest repoint file without the path prefix
    return basename(latest[2])


def get_upstream_dependency_inputs(
    dependencies: list,
    start_date: datetime,
    end_date: datetime,
):
    """Construct a ProcessingInputCollection of dependency files.

    # TODO: Will query spice db in the future
    For each dependency, query for existing files in s3 and add any matching files
    found to a ProcessingInputCollection.

    Parameters
    ----------
    dependencies : list
        List of dependency dictionaries either downstream or upstream from the
        dependency in the query parameters.
    start_date : datetime
        Start date to find dependent files with.
    end_date : datetime
        End date to find dependent files with.

    Returns
    -------
    ProcessingInputCollection
        Dependency files that can include Ancillary, SPICE, or Science inputs.
    """
    dependency_inputs = processing_input.ProcessingInputCollection()
    with db.Session() as session:
        # -----------------------------
        # Check for SPICE dependencies
        # -----------------------------
        # If spin is a dependency, query spin table for given date range
        has_spin_dep = any(dep["data_source"] == "spin" for dep in dependencies)
        if has_spin_dep:
            logger.info("Looking for spin files")
            spin_files = get_spin_files(session, start_date, end_date)
            if not spin_files:
                logger.info(f"No spin files found for {start_date} to {end_date}")
                return None
            logger.info(f"Found spin files: {spin_files}. Adding to collection.")
            dependency_inputs.add(processing_input.SpinInput(*spin_files))

        # If repoint is a dependency, query s3 for latest repoint file
        has_repoint_dep = any(dep["data_source"] == "repoint" for dep in dependencies)
        if has_repoint_dep:
            latest_repoint_file = get_latest_repoint_file(end_date)
            if latest_repoint_file is None:
                logger.info(f"No repoint file found for {start_date} to {end_date}")
                return None
            logger.info(
                f"Found repoint file: {latest_repoint_file}. Adding to collection."
            )
            dependency_inputs.add(processing_input.RepointInput(latest_repoint_file))

        # Otherwise, combine rest of kernels types and query metakernel lambda
        # for given date range
        has_kernel_dep = any(
            dep["data_source"] != "spin"
            and dep["data_source"] != "repoint"
            and dep["data_type"] == "spice"
            for dep in dependencies
        )
        if has_kernel_dep:
            combined_kernel_sources = combine_kernel_sources(dependencies)

            # convert start_date and end_date in seconds after j2000.
            # TODO: remove this once Bryan changes takes in 'yyyymmdd' format
            def yyyymmdd_to_seconds_since_j2000(date_str: str) -> float:
                # Parse input date string
                dt = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)

                # Define J2000 epoch: 2000-01-01T12:00:00 UTC
                j2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

                # Compute seconds difference
                delta = dt - j2000
                return delta.total_seconds()

            start_time = yyyymmdd_to_seconds_since_j2000(start_date.strftime("%Y%m%d"))
            end_time = yyyymmdd_to_seconds_since_j2000(end_date.strftime("%Y%m%d"))
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
                logger.info(
                    f"Error querying metakernel lambda: {metakernel_response['body']}"
                )
                return None
            metakernel_files = json.loads(metakernel_response["body"])
            logger.info(
                f"Found metakernel files: {metakernel_files}. Adding to collection."
            )
            dependency_inputs.add(processing_input.SPICEInput(*metakernel_files))

        # ---------------------------------
        # Check for non-spice dependencies
        # ---------------------------------
        non_spice_dependencies = [
            dep
            for dep in dependencies
            if dep["data_type"] not in ["spice", "spin", "repoint"]
        ]
        for dep in non_spice_dependencies:
            relationship = dep["relationship"]

            dep_string = f"{dep=}\n{start_date=}\n{end_date=}"

            logger.info(f"Searching for files matching dep={dep_string}")

            records = get_files(session, dep, start_date, end_date)
            if not records and relationship == Relationship.HARD:
                logger.info(f"No records found for dependency: {dep_string}")
                return None

            elif not records:
                continue

            filenames = [basename(record.file_path) for record in records]
            logger.info(f"Found filenames: {filenames}. Adding to collection.")

            # Create a processingInput instance and add it to the collection
            if dep["data_type"] == DataType.ANCILLARY:
                dependency_inputs.add(processing_input.AncillaryInput(*filenames))
            else:
                dependency_inputs.add(processing_input.ScienceInput(*filenames))

    return dependency_inputs


def get_files(
    session: db.Session,
    dependency: dict,
    start_date: datetime,
    end_date: datetime,
):
    """Query to database to get ScienceFile or AncillaryFile records.

    Parameters
    ----------
    session : orm session
        Database session.
    dependency : dict
        dictionary containing:
        data_source : str
            Source name.
        data_type : str
            Data type.
        descriptor : str
            Data descriptor.
    start_date : datetime
        Start date of the event data.
    end_date: datetime
        End date of the event data.

    Returns
    -------
    records : list[Union[models.ScienceFiles, models.AncillaryFiles]]
        The ScienceFiles or AncillaryFiles records matching the query criteria.
    """
    type_specific_conditions = []
    if dependency["data_type"] == DataType.ANCILLARY:
        table = models.AncillaryFiles
        # Query for ancillary files where the start_date is less than or equal to
        # the input end_date, and the end_date is either greater than or equal to the
        # input start_date or is None. For example, if the input start_date is
        # '20240524' and the end_date is '20240527', the query could return an ancillary
        # file with the date range ('20240525', '20240528').
        type_specific_conditions.append(
            and_(
                table.start_date <= end_date,
                or_(table.end_date >= start_date, table.end_date.is_(None)),
            )
        )
    else:
        table = models.ScienceFiles
        type_specific_conditions.append(table.data_level == dependency["data_type"])
        # Find files with start dates in the start_date and end_date range
        type_specific_conditions.append(
            and_(
                models.ScienceFiles.start_date >= start_date,
                models.ScienceFiles.start_date <= end_date,
            )
        )
    filter_conditions = [
        table.instrument == dependency["data_source"],
        table.descriptor == dependency["descriptor"],
        *type_specific_conditions,
    ]
    # Group by start_date
    # E.g.,
    # We are querying for swe l1a files with start dates in range (20240510, 20240513)
    # The following swe files are found:
    #    - swe_l1a_sci_20240511_v001
    #    - swe_l1a_sci_20240511_v002
    #    - swe_l1a_sci_20240512_v001
    #    - swe_l1a_sci_20240512_v004
    # We only want to return the latest versions per start date
    #    - swe_l1a_sci_20240511_v002
    #    - swe_l1a_sci_20240512_v004
    max_version_query = (
        session.query(table.start_date, func.max(table.version).label("latest_version"))
        .filter(*filter_conditions)
        .group_by(table.start_date)
        .subquery()
    )
    # Query records
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

    # If the dependency is ancillary, only return the one with the latest start_date.
    if dependency["data_type"] == DataType.ANCILLARY:
        records = sorted(records, key=lambda x: x.start_date, reverse=True)[0:1]

    return records


def get_jobs(
    dependency_type: str,
    relationship: str,
    data_source: str,
    data_type: str,
    descriptor: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list | ProcessingInputCollection | None:
    """Get dependencies for the given inputs.

    Parameters
    ----------
    dependency_type : str
        Whether it's UPSTREAM or DOWNSTREAM dependency.
    relationship : str
        Whether it's HARD, SOFT_TRIGGER, or SOFT_NO_TRIGGER dependency.
        If "ALL" is provided, dependencies for all valid relationships
        (HARD, SOFT_TRIGGER, SOFT_NO_TRIGGER) will be returned.
    data_source : str
        Source name of the data product.
    data_type : str
        Data type of the data product.
    descriptor : str
        Descriptor of the data product.
    start_date : str, optional
        Start date to find dependent files with, in YYYYMMDD format.
    end_date : str, optional
        End date to find dependent files with, in YYYYMMDD format. Required if
        start_date is provided.

    Returns
    -------
    dependencies : list or ProcessingInputCollection or None
        If "start_date" is not supplied return list of dictionaries containing
        the dependencies information like this:
            [
                {
                    "data_source": "hit",
                    "data_type": "l1a",
                    "descriptor": "all",
                    "relationship": "HARD",
                },
                {
                    "data_source": "hit",
                    "data_type": "l1b",
                    "descriptor": "hk",
                    "relationship": "HARD",
                },
                {
                    "data_source": "sc_attitude",
                    "data_type": "spice",
                    "descriptor": "historical",
                    "relationship": "HARD",
                },
            ]
        If "start_date" is supplied, "end_date" is required. Return a
        ProcessingInputCollection of files that exist on s3.
            [
                {
                    "type": "ancillary",
                    "files": [
                        "imap_mag_l1b-cal_20250101_v001.cdf",
                        "imap_mag_l1b-cal_20250103-20250104_v002.cdf"
                    ]
                },
                {
                    "type": "ancillary",
                    "files": [
                        "imap_mag_l1b-lut_20250101_v001.cdf",
                    ]
                },
                {
                    "type": "science",
                    "files": [
                        "imap_mag_l1a_norm-magi_20240312_v000.cdf",
                        "imap_mag_l1a_norm-magi_20240312_v001.cdf"
                    ]
                }
            ]


    """
    logger.info(
        f"{data_source=}, {data_type=}, {descriptor=}, {dependency_type=},"
        f" {relationship=}"
    )

    dependencies = get_dependencies(
        (data_source, data_type, descriptor),
        dependency_type,
        relationship,
    )

    if dependencies is None:
        logger.warning("Failed to load dependencies")
        raise ValueError("Failed to load dependencies")

    # If start_date is supplied, check for the version and end_date.
    start_date = datetime.strptime(start_date, "%Y%m%d") if start_date else None
    if start_date is None:
        return dependencies

    if not end_date:
        raise ValueError(
            "end_date not found. If 'start_date' is supplied, 'end_date' is required."
        )
    end_date = datetime.strptime(end_date, "%Y%m%d")

    upstream_dependencies_output = get_upstream_dependency_inputs(
        dependencies=dependencies,
        start_date=start_date,
        end_date=end_date,
    )

    logger.info(f"Found dependencies: {dependencies}.")
    return upstream_dependencies_output
