"""Contains all functions needed to create an IMAP metakernel."""

import logging
import math
import json
from datetime import datetime
from pathlib import Path

import spiceypy
from imap_data_access import SPICEFilePath
from .metakernel import MetaKernel
from ..api_lambdas import spice_query_api

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
        static_spice_file = spice_query_api.lambda_handler({"queryStringParameters": {"type": type}}, None)
        metakernel.load_spice(static_spice_file, type, "timestamp", "file_intervals_j2000")

    for ephem_type in [
        "ephemeris_reconstructed",
        "ephemeris_nominal",
        "ephemeris_predicted",
        "ephemeris_90days",
        "ephemeris_long",
        "ephemeris_launch",
    ]:
        if len(metakernel.spice_gaps["spacecraft_ephemeris"]) > 0:
            ephem_files = spice_query_api.lambda_handler({"queryStringParameters": {"start_time": start_time_j2000, "end_time":end_time_j2000, "type":ephem_type}})
            metakernel.load_spice(json.loads(ephem_files['body']), "spacecraft_ephemeris", "timestamp", "file_intervals_j2000")

    for attitude_type in ["attitude_history", "attitude_predict"]:
        if len(metakernel.spice_gaps["spacecraft_attitude"]) > 0:
            attitude_files = spice_query_api.lambda_handler({"queryStringParameters": {"start_time": start_time_j2000, "end_time":end_time_j2000, "type":attitude_type}})
            metakernel.load_spice(json.loads(attitude_files['body']), "spacecraft_attitude", "timestamp", "file_intervals_j2000")

    return metakernel
