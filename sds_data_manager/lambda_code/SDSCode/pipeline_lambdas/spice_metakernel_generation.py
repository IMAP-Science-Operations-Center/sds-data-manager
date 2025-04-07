"""Contains all functions needed to create an IMAP metakernel."""

import logging
import math
from datetime import datetime
from pathlib import Path

import spiceypy
from imap_data_access import SPICEFilePath
from sqlalchemy import select, func

from ..database import database as db
from ..database import models
from .metakernel import MetaKernel

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


SPACECRAFT_ID = -43


def create_imap_metakernel(year: int, spice_directory: Path) -> tuple[str, str]:
    """Create an IMAP Metakernel.

    Parameters
    ----------
    year: int
        The year to make the file
    spice_directory: Path
        The path to the local SPICE directory

    Returns
    -------
    mk_filename: str
        The name of the new metakernel file
    rendered_file: str
        The contents to put in the file
    """
    # Query for most recent MK file in the current year
    logger.info("Checking for existing metakernels in %s", year)

    # Take a look at the directory directly
    metakernel_directory = spice_directory / "mk"
    sorted_mks = sorted([f for f in metakernel_directory.iterdir() if f.is_file()])
    if sorted_mks:
        most_recent_mk = metakernel_directory / sorted_mks[-1]
    else:
        most_recent_mk = None

    # Determine version number to use
    if most_recent_mk:
        logger.info("Metakernel exists for %s", year)
        metakernel_info = SPICEFilePath(most_recent_mk)
        most_recent_ver = int(metakernel_info.spice_metadata["version"])
        # Tick up the version number
        most_recent_ver += 1
        most_recent_ver = str(most_recent_ver).zfill(3)
    else:
        logger.info("No metakernal exists for %s, creating one now", year)
        most_recent_ver = str(1).zfill(3)

    # Determine file name
    mk_filename = f"imap_{year}_v{most_recent_ver}.tm"

    # Create a SPICE metakernel
    metakernel = metakernel_builder(
        start_time=datetime(year, 1, 1), end_time=datetime(year + 1, 1, 1)
    )
    if metakernel is not None:
        rendered_file = metakernel.return_tm_file(base_path=spice_directory)
        logger.info("Rendered new metakernel %s", rendered_file)
    else:
        rendered_file = None

    if most_recent_mk is not None and rendered_file is not None:
        # Check if identical to previous version, if so, do not create a new one.
        with open(str(most_recent_mk)) as f:
            most_recent_mk_contents = f.read()

        # Ignore the first 100 characters of the file, that contains
        # information about the date that the metakernel was generated
        logger.info(f"Old file: {most_recent_mk_contents[110:]}")
        logger.info(f"New file: {rendered_file[110:]}")

        if most_recent_mk_contents[110:] == rendered_file[110:]:
            logger.info(
                "New SPICE file is identical to old SPICE file, continuing "
                "without generating new Metakernel."
            )
            return None, None

    return mk_filename, rendered_file


def convert_spice_metadata_model_to_dict(file: models.SPICEFiles) -> dict:
    """Convert a sqlalchemy query to SPICEFiles to a dictionary.

    Paramters
    ----------
    file: models.SPICEFiles
        A single row from the SPICEFiles table

    Returns
    -------
    spice_file_dict: dict
        The SPICE file query as a dictionary
    """
    spice_file_dict = {
        "file_name": (
            SPICEFilePath(file.file_name).construct_path().parent.name
            + "/"
            + file.file_name
        ),
        "file_root": file.file_root,
        "kernel_type": file.kernel_type,
        "version": file.version,
        "min_date_j2000": file.min_date_j2000,
        "max_date_j2000": file.max_date_j2000,
        "file_intervals_j2000": file.file_intervals_j2000,
        "min_date_datetime": file.min_date_datetime.strftime("%Y-%m-%d, %H:%M:%S"),
        "max_date_datetime": file.max_date_datetime.strftime("%Y-%m-%d, %H:%M:%S"),
        "min_date_sclk": file.min_date_sclk,
        "max_date_sclk": file.max_date_sclk,
        "file_intervals_sclk": file.file_intervals_sclk,
        "sclk_kernel": file.sclk_kernel,
        "lsk_kernel": file.lsk_kernel,
        "ingestion_date": file.ingestion_date.strftime("%Y-%m-%d, %H:%M:%S"),
        "timestamp": file.ingestion_date.timestamp(),
    }
    return spice_file_dict


def query_spice_metadata_database(
    start_time: int = 1000, end_time: int = 31525416070, type: str = "", latest=True
) -> dict:
    """Query SPICEFiles table for time and type.

    Parameters
    ----------
    start_time: int
        The starting time in J2000 to limit the query
    end_time: int
        The ending time in J2000 to limit the query
    type: str | None
        The type of file to query for. If None, queries all file types.
    latest: bool
        Whether or not to include lower versions of the same file.

    Returns
    -------
    spice_file_dict: dict
        A dictionary of the form {'file1': {metadata1}, 'file2': {metadata2}, ... etc}
        Where the metadata is a dictionary form of the data in the database row
    """
    with db.Session() as session, session.begin():
        query = select(models.SPICEFiles)

        query = query.where(models.SPICEFiles.min_date_j2000 <= end_time)
        query = query.where(models.SPICEFiles.max_date_j2000 >= start_time)

        if type:
            query = query.where(models.SPICEFiles.kernel_type == type)
        if latest:
            # --- 2. Make a subquery that gives us (file_root, MAX(version))
            latest_versions_subq = (
                session.query(
                    models.SPICEFiles.file_root,
                    func.max(models.SPICEFiles.version).label("max_version")
                )
                .group_by(models.SPICEFiles.file_root)
                .subquery()
            )

            # --- 3. Join main query to subquery so that we only keep rows
            #         with the matching max version for each file_root
            query = (
                query
                .join(
                    latest_versions_subq,
                    (models.SPICEFiles.file_root == latest_versions_subq.c.file_root)
                    & (models.SPICEFiles.version == latest_versions_subq.c.max_version)
                )
            )
        results = session.execute(query).scalars().all()
        
        spice_file_dict = {}
        for n in results:
            metadata = convert_spice_metadata_model_to_dict(n)
            spice_file_dict[metadata["file_name"]] = metadata

        return spice_file_dict


def metakernel_builder(start_time: datetime, end_time: datetime) -> MetaKernel:
    """Create a MetaKernel class and inserts files into it."""
    start_time_j2000 = math.floor(float(spiceypy.datetime2et(start_time)))
    end_time_j2000 = math.floor(float(spiceypy.datetime2et(end_time)))

    # Create the Metakernel class
    metakernel = MetaKernel(
        start_time_j2000,
        end_time_j2000,
        allowed_spice_types=[
            "leapseconds",
            "planetary_constants",
            "frames",
            "spacecraft_clock",
            "planetary_ephemeris",
            "spacecraft_ephemeris",
            "spacecraft_attitude",
        ],
    )

    static_files_load_order = [
        "leapseconds",
        "planetary_constants",
        "frames",
        "spacecraft_clock",
        "planetary_ephemeris",
    ]

    for type in static_files_load_order:
        static_spice_file = query_spice_metadata_database(type=type)
        metakernel.load_spice(static_spice_file, type, 'timestamp')

    for ephem_type in [
        "ephemeris_reconstructed",
        "ephemeris_nominal",
        "ephemeris_predicted",
        "ephemeris_90days",
        "ephemeris_long",
        "ephemeris_launch",
    ]:
        if len(metakernel.spice_gaps["spacecraft_ephemeris"]) > 0:
            ephem_files = query_spice_metadata_database(
                start_time=start_time_j2000, end_time=end_time_j2000, type=ephem_type
            )
            metakernel.load_spice(ephem_files, "spacecraft_ephemeris", "timestamp")

    for attitude_type in ["attitude_history", "attitude_predict"]:
        if len(metakernel.spice_gaps["spacecraft_attitude"]) > 0:
            attitude_files = query_spice_metadata_database(
                start_time=start_time_j2000, end_time=end_time_j2000, type=attitude_type
            )
            metakernel.load_spice(attitude_files, "spacecraft_attitude", "timestamp")

    return metakernel
