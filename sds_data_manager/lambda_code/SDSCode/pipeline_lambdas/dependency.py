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
from sqlalchemy import and_, func, or_, select

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
        2. SOFT - the data is optional for the pipeline to run
    """

    HARD: str = "HARD"
    SOFT: str = "SOFT"

    @property
    def valid_relationship(self) -> list[str]:
        """Add data relationships.

        Returns
        -------
        list[str]
            list of valid data relationships.
        """
        return [self.HARD, self.SOFT]


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
        Whether it's HARD or SOFT dependency.
        HARD means data is required for pipeline and SOFT
        means data is optional for pipeline.

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

    dependencies = dependency_config.dependencies[relationship][dependency_type].get(
        node, []
    )
    # Add keys for a dict-like representation
    dependencies = [
        {"data_source": dep[0], "data_type": dep[1], "descriptor": dep[2]}
        for dep in dependencies
    ]

    return dependencies


def filter_primary_science_dependencies(
    session: db.Session, records: list, query_data_type: str, query_descriptor
):
    """Filter primary science dependencies for unprocessed downstream dependencies.

    Parameters
    ----------
    session : orm session
        Database session.
    records : list[models.ScienceFiles]
        Science file records.
    query_data_type : str
        The data_type of the dependency used to query the api.
    query_descriptor
        The descriptor of the dependency used to query the api.

    Returns
    -------
    list[str]
        Upstream primary source filenames that have downstream dependencies that need
        to be processed.
    """
    # TODO create a downstream dependency instead of combining the query and records.
    files = []
    for record in records:
        # check in the science table if the upstream primary source dependency
        # already exists.
        query = select(models.ScienceFiles.__table__).where(
            models.ScienceFiles.instrument == record.instrument,
            models.ScienceFiles.descriptor == query_descriptor,
            # Use the query data type instead of the current record.
            models.ScienceFiles.data_level == query_data_type,
            models.ScienceFiles.start_date == record.start_date,
            models.ScienceFiles.version == record.version,
        )
        # If the upstream primary source dependency does not exist, add it to the list
        # of files to return.
        # This indicates that the upstream primary source needs to be processed.
        upstream_primary_source = session.execute(query).first()

        if not upstream_primary_source:
            files.append(basename(record.file_path))

    return files


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
        True if dependency is a primary science dependency, False otherwise

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


def get_dependency_processing_input(
    query_params: dict,
    dependencies: list,
    start_date: datetime,
    version: str,
    trigger_type: str,
    end_date: Optional[datetime] = None,
):
    """Construct a ProcessingInputCollection of dependency files.

    # TODO: Will query spice db in the future
    For each dependency, query for existing files in s3 and add any matching files
    found to a ProcessingInputCollection.

    Parameters
    ----------
    query_params : dict
        Query parameters received from the API call describing either an upstream or
        downstream dependency.
    dependencies : list
        List of dependency dictionaries either downstream or upstream from the
        dependency in the query parameters.
    start_date : datetime
        Start date to find dependent files with.
    version : str
        Version to find dependent files with.
    trigger_type : str
        Data type of the file that triggered the batch starter.
    end_date : datetime, optional
        End date to find dependent files with.

    Returns
    -------
    ProcessingInputCollection
        Dependency files that can include Ancillary, SPICE, or Science inputs.
    """
    dependency_inputs = processing_input.ProcessingInputCollection()
    inputs = []
    with db.Session() as session:
        for dep in dependencies:
            # Check if the dependency is a primary science dependency and if the file
            # source that triggered the batch stater is equal to the dependency source.
            # If true, we can find science files with the exact start date and version
            # used in the query.

            # This check is necessary because the start date and version are extracted
            # from the trigger file. If the trigger file is either an ancillary file
            # (including science files from a different source) or SPICE, the exact
            # start date and version cannot be used to find the science file because
            # the dates are not guaranteed to correspond.
            primary_sci_dep = primary_science_dep(query_params, dep)
            if primary_sci_dep and trigger_type == dep["data_type"]:
                primary_sci_trigger = True
            else:
                primary_sci_trigger = False

            logger.info(
                f"Searching for files matching dep={dep}\n"
                f"start_date={start_date}\n"
                f"version={version}\n"
                f"end_date={end_date}\n"
                f"primary_sci_trigger={primary_sci_trigger}\n"
                f"primary_sci_dep={primary_sci_dep}"
            )
            records = get_files(
                session,
                dep,
                start_date,
                version,
                end_date,
                primary_sci_trigger,
                primary_sci_dep,
            )
            if not records:
                # TODO change return
                logger.info(
                    "No records found for dependency. Returning empty collection."
                )
                return dependency_inputs

            filenames = [basename(record.file_path) for record in records]
            logger.info(f"Found filenames: {filenames}. Adding to collection.")
            # If this is a primary science dependency, filter files for ones that have a
            # downstream counterpart that needs to be processed.
            # E.g. if imap_mag_l1d-sci_0250105.cdf file triggers batch starter,
            # This could potentially trigger multiple swe l1b files that have been
            # waiting. E.g.,
            #    - imap_swe_l1b_sci_20250102.cdf
            #    - imap_swe_l1b_sci_20250103.cdf
            #    - imap_swe_l1b_sci_20250104.cdf
            # Swe l1a is an upstream for swe l1b and get_files() will return all swe l1a
            # records with start dates before 0250105.
            # This list can be narrowed by calling filter_primary_science_dependencies()
            # It will look for each l1a file's l1b counter-part in the science files
            # table. If the file already exists, the l1a file is ignored.
            if primary_sci_dep:
                filenames = filter_primary_science_dependencies(
                    session,
                    records,
                    query_params["data_type"],
                    query_params["descriptor"],
                )
                if not filenames:
                    logger.info(
                        "Primary dependency files already processed. Returning empty "
                        "collection."
                    )
                    return dependency_inputs
            # Create a processingInput instance and add it to the collection
            if dep["data_type"] == DataType.ANCILLARY:
                inputs.append(processing_input.AncillaryInput(*filenames))
            else:
                inputs.append(processing_input.ScienceInput(*filenames))
    dependency_inputs.add(inputs)
    return dependency_inputs


def get_files(
    session: db.Session,
    dependency: dict,
    start_date: datetime,
    version: str,
    end_date: Optional[datetime] = None,
    primary_sci_trigger: Optional[bool] = False,
    primary_sci_dep: Optional[bool] = False,
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
    version : str
        Version of the event data.
    end_date: datetime, optional
        End date of the event data.
    primary_sci_trigger: bool, optional
        When True, query for science files with a match to the start time and version
        because it is assumed that the dependency is a primary science dependency and
        the trigger source is of the same data_source. Default is False.
    primary_sci_dep : bool, optional
        Controls how science files are queried based on their start dates.
        When True, it is assumed that the query file is a primary science dependency.
        Look for science files with start_date >= query start_date.
        When False, treat science files from different sources like ancillary files
        Look for science files with start_date <= query start_date.

    Returns
    -------
    records : list[Union[models.ScienceFiles, models.AncillaryFiles]]
        The ScienceFiles or AncillaryFiles records matching the query criteria.
    """
    return_latest_ancillary = False
    type_specific_conditions = []
    if dependency["data_type"] == DataType.ANCILLARY:
        table = models.AncillaryFiles
        # Query for ancillary files whose ranges cover the
        # start date.
        # E.g., if the start date is '20250102', the query could return an ancillary
        # file with the date range ('20250101', '20250103')
        # TODO this could return all ancillary files with start dates before 20250102
        type_specific_conditions.append(
            and_(
                table.start_date <= start_date,
                or_(table.end_date >= start_date, table.end_date.is_(None)),
            )
        )
        return_latest_ancillary = True
    else:
        table = models.ScienceFiles
        type_specific_conditions.append(table.data_level == dependency["data_type"])
        if primary_sci_trigger:
            # Query for science files matching the start date and version
            # Example:
            # Trigger source: swe_l0_raw_20250102_v001.pkts
            # Downstream: swe_l1a_sci
            # Upstream: Look for swe_l0_raw with start date == 20250102 and
            # version == v001

            type_specific_conditions.extend(
                [
                    models.ScienceFiles.start_date == start_date,
                    # TODO revisit - Mag L1C case.
                    table.version == version,
                ]
            )
        elif end_date:
            # Find files that are downstream from an ancillary file
            # Query for science files with a start date later or equal to the
            # ancillary start date and less than the ancillary end date.
            # Example:
            # Trigger source: swe_l1b-flight-cal_20250102-20250104
            # Downstream: swe_l1b_sci
            # Upstream: Look for swe_l1a_sci with start dates in range
            # 20250102-20250104
            type_specific_conditions.append(
                and_(
                    models.ScienceFiles.start_date >= start_date,
                    models.ScienceFiles.start_date <= end_date,
                )
            )
        elif primary_sci_dep:
            # Find primary source science files that are greater or equal than
            # the start_date (start_date comes from an ancillary file, so we
            # cannot use the exact date.)
            # Example:
            # Trigger source: mag_l1b_sci_20240510_v001.cdf
            # Downstream: swe_l1b_sci
            # Upstream: Look for swe_l1a_sci with start dates greater than or
            # equal to 20240510
            type_specific_conditions.append(
                models.ScienceFiles.start_date >= start_date
            )
        else:
            # Science files of another source are treated like ancillary files.
            # Look for science files that are older than the start_date
            # Example:
            # Trigger source: swe_l0_raw_20250102_v001.pkts
            # Downstream: swe_l1a_sci
            # Upstream: Look for mag_l1d_sci with start dates less than or equal
            # to 20250102
            type_specific_conditions.append(
                models.ScienceFiles.start_date <= start_date
            )
            return_latest_ancillary = True

    filter_conditions = [
        table.instrument == dependency["data_source"],
        table.descriptor == dependency["descriptor"],
        *type_specific_conditions,
    ]
    # TODO check if version is supplied - otherwise get max version.
    # Only group by start date if return_latest_ancillary is false.
    # If true, we only want to return one ancillary file (including science files of
    # another instrument) with the most recent start date and greatest version number,
    # otherwise we want to return the max version for each start_date.
    if return_latest_ancillary:
        # We are querying for swe_l1b-in-flight-calibration ancillary files with start
        # dates less than or equal to 20250102 and want to run swe l1b.
        # The following swe files are found:
        #    - swe_l1b_in-flight-cal_20240511_v001
        #    - swe_l1a_in-flight-cal_20240511_v002
        #    - swe_l1a_in-flight-cal_20240512_v001
        #    - swe_l1a_in-flight-cal_20240512_v004
        # We only want to return the most recent start date with the max version
        #    - swe_l1a_sci_20240512_v004
        # First, find the maximum start_date
        max_start_date = (
            session.query(func.max(table.start_date))
            .filter(*filter_conditions)
            .scalar()
        )
        # Find the maximum version for that start_date
        max_version = (
            session.query(func.max(table.version))
            .filter(table.start_date == max_start_date, *filter_conditions)
            .scalar()
        )

        latest_query = session.query(table).filter(
            table.start_date == max_start_date,
            table.version == max_version,
        )
    else:
        # Group by start_date
        # E.g.,
        # We are querying for swe l1a science files with start dates greater than or
        # equal to 20240510 and want to run swe l1b sci jobs.
        # The following swe files are found:
        #    - swe_l1a_sci_20240511_v001
        #    - swe_l1a_sci_20240511_v002
        #    - swe_l1a_sci_20240512_v001
        #    - swe_l1a_sci_20240512_v004
        # We only want to return the latest versions per start date
        #    - swe_l1a_sci_20240511_v002
        #    - swe_l1a_sci_20240512_v004
        max_version_query = (
            session.query(
                table.start_date, func.max(table.version).label("latest_version")
            )
            .filter(*filter_conditions)
            .group_by(table.start_date)
            .subquery()
        )
        # Query records
        latest_query = session.query(table).join(
            max_version_query,
            (table.start_date == max_version_query.c.start_date)
            & (table.version == max_version_query.c.latest_version),
        )

    records = latest_query.filter(*filter_conditions).all()

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
                "version": "v001" (optional)
                "trigger_type": "mag" (optional)
            }
        "start_time", "end_time", and "version" are optional.
        If "start_time" is supplied, then "version" is required.
       "trigger_type" is the source of the file that triggered the batch starter

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
                },
                {
                    "data_source": "hit",
                    "data_type": "l1b",
                    "descriptor": "hk",
                },
                {
                    "data_source": "sc_attitude",
                    "data_type": "spice",
                    "descriptor": "historical",
                },
            ]
        If "start_date" is supplied, "version" and "ancillary_trigger" are required and
        "end_date" is optional. Return a ProcessingInputCollection of files that exist
        on s3.
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
    if start_date:
        version = query_params.get("version")
        if not version:
            return {
                "statusCode": 400,  # Client error
                "body": "Version not found. If 'start_date' is supplied, 'version' is"
                " required.",
            }
        trigger_type = query_params.get("trigger_type")
        if not trigger_type:
            return {
                "statusCode": 400,  # Client error
                "body": "trigger_type not found. If 'start_date' is supplied, "
                "'trigger_type' is required.",
            }

        # Get and convert end_date in one line if it exists
        end_date = (
            datetime.strptime(query_params.get("end_date"), "%Y%m%d")
            if query_params.get("end_date")
            else None
        )
        # TODO this only works for upstream deps right now. Do we need to ever get files
        # for downstream?
        dependencies_output = get_dependency_processing_input(
            query_params, dependencies, start_date, version, trigger_type, end_date
        )

        dependencies_output = dependencies_output.serialize()
    else:
        dependencies_output = json.dumps(dependencies)

    logger.info(f"Found dependencies: {dependencies} for {query_params}.")

    # TODO: add reprocessing dependencies are handled here
    return {
        "statusCode": 200,  # Success
        "body": dependencies_output,
    }
