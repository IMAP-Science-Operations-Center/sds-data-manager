"""Dependency tracking module."""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from os.path import basename
from pathlib import Path
from typing import Optional

import imap_data_access
from imap_data_access import processing_input
from sqlalchemy import and_, func, or_

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

    SC_ATTITUDE: str = "sc_attitude"
    SC_EPHEMERIS: str = "sc_ephemeris"
    PLANET_EPHEMERIS: str = "planet_ephemeris"
    TIME_KERNEL: str = "time_kernel"
    THRUSTER_FIRE_KERNEL: str = "thruster_fire_kernel"
    SC_SPIN: str = "sc_spin"
    SC_REPOINT: str = "sc_repoint"
    SC_POINTING_FRAME: str = "sc_pointing_frame"

    @property
    def valid_source(self) -> list[str]:
        """Add data sources.

        Returns
        -------
        list[str]
            list of valid data sources.
        """
        return [
            self.SC_ATTITUDE,
            self.SC_EPHEMERIS,
            self.PLANET_EPHEMERIS,
            self.TIME_KERNEL,
            self.THRUSTER_FIRE_KERNEL,
            self.SC_SPIN,
            self.SC_REPOINT,
            self.SC_POINTING_FRAME,
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

    SPICE: str = "spice"
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

    HARD: str = "HARD"
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
        return [self.HARD, self.SOFT_TRIGGER, self.SOFT_NO_TRIGGER]


@dataclass
class DependencyType:
    """Valid data dependency type for dependency tracking.

    Valid data dependency types are:
        1. UPSTREAM - Processed product to start current product's process
        2. DOWNSTREAM - future file that needs current file to start its process
    """

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

                # validate node
                if not self._validate_node(parent_node) or not self._validate_node(
                    child_node
                ):
                    logger.debug(
                        f"Parent node: {parent_node}, Child node: {child_node}"
                    )
                    raise ValueError(
                        "Data product must have: (source, type, descriptor)"
                    )

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

        Returns
        -------
        bool
            True if node is valid, False otherwise.
        """
        if len(node) != 3:
            logger.debug("Missing data source, data type, or descriptor")
            return False
        if node[0] not in self.data_source.valid_source:
            logger.debug(f"Invalid data source: {node[0]}")
            return False
        if node[1] not in self.data_type.valid_type:
            logger.debug(f"Invalid data type: {node[1]}")
            return False
        # TODO: Add descriptor validation once we define all data product's
        # data descriptor.
        return True


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
    try:
        dependency_config = DependencyConfig()
    except Exception as e:
        logger.error(f"Error loading dependencies: {e!s}")
        return None

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
        Query parameters received from API calls.
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


def get_upstream_dependency_inputs(
    dependencies: list,
    start_date: datetime,
    end_date: Optional[datetime] = None,
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
    end_date : datetime, optional
        End date to find dependent files with.

    Returns
    -------
    ProcessingInputCollection
        Dependency files that can include Ancillary, SPICE, or Science inputs.
    """
    dependency_inputs = processing_input.ProcessingInputCollection()
    with db.Session() as session:
        for dep in dependencies:
            relationship = dep["relationship"]

            dep_string = f"{dep=}\n{start_date=}\n{end_date=}"

            logger.info(f"Searching for files matching dep={dep_string}")

            records = get_files(session, dep, start_date, end_date)
            if not records and relationship == Relationship.HARD:
                info = f"No records found for dependency: {dep_string}"
                logger.info(dep_string)
                return info

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
    ancillary = False
    type_specific_conditions = []
    if dependency["data_type"] == DataType.ANCILLARY:
        ancillary = True
        table = models.AncillaryFiles
        # Query for ancillary files whose ranges cover the
        # start date and end date.
        # E.g., if the start date is '20250102', the query could return an ancillary
        # file with the date range ('20250101', '20250103')
        type_specific_conditions.append(
            and_(
                table.start_date <= start_date,
                or_(table.end_date >= end_date, table.end_date.is_(None)),
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
    if ancillary:
        records = sorted(records, key=lambda x: x.start_date, reverse=True)[0:1]

    return records


def lambda_handler(event, context):
    """Lambda handler for dependency tracking.

    Parameters
    ----------
    event : dict
        If dependency is requested, event input will be:
            {
                "data_source": "hit",
                "data_type": "l0",
                "descriptor": "raw",
                "dependency_type": "UPSTREAM",
                "relationship": "HARD",
                "start_time": "20250101", (optional)
                "end_time": "20250102", (optional)
            }
        "start_time", and "end_time", are optional.

    context : dict
        Context dictionary.

    Returns
    -------
    dependencies : list or ProcessingInputCollection
        If "start_date" is not supplied return list of dictionaries:
        statusCode and body containing list of dictionary containing
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
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    query_params = event["queryStringParameters"]
    dependencies = get_dependencies(
        (
            query_params["data_source"],
            query_params["data_type"],
            query_params["descriptor"],
        ),
        query_params["dependency_type"],
        query_params["relationship"],
    )

    if dependencies is None:
        return {
            "statusCode": 500,
            "body": "Failed to load dependencies",
        }
    # If start_date is supplied, check for the version and end_date.
    start_date = (
        datetime.strptime(query_params["start_date"], "%Y%m%d")
        if query_params.get("start_date")
        else None
    )
    if start_date is None:
        return {
            "statusCode": 200,  # Success
            "body": json.dumps(dependencies),
        }
    end_date = query_params.get("end_date")
    if not end_date:
        return {
            "statusCode": 400,  # Client error
            "body": "end_date not found. If 'start_date' is supplied, "
            "'end_date' is required.",
        }
    end_date = datetime.strptime(end_date, "%Y%m%d")

    upstream_dependencies_output = get_upstream_dependency_inputs(
        dependencies=dependencies,
        start_date=start_date,
        end_date=end_date,
    )
    if isinstance(upstream_dependencies_output, str):
        return {
            "statusCode": 206,  # Partial content
            "body": upstream_dependencies_output,
        }
    else:
        logger.info(f"Found dependencies: {dependencies} for {query_params}.")
        upstream_dependencies_output = upstream_dependencies_output.serialize()
        return {
            "statusCode": 200,  # Success
            "body": upstream_dependencies_output,
        }
