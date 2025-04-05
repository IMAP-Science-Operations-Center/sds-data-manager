"""Contains all functions needed to create an IMAP metakernel."""

import json
import logging
import math
import textwrap
from datetime import datetime
from pathlib import Path

import spiceypy
from imap_data_access import SPICEFilePath
from sqlalchemy import select

from ..database import database as db
from ..database import models

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


SPACECRAFT_ID = -43


class MetaKernel:
    """Class for generating a metakernels from SPICE files."""

    def __init__(self, start_time: int, end_time: int, allowed_spice_types: list[str]):
        """Initialize the Metakernel.

        Parameters
        ----------
        start_time: int
            The start_time in seconds after j2000
        end_time: int
            The end_time in seconds after j2000
        allowed_spice_types: list[str]
            A list of strings that represent the allowed types of SPICE files
        """
        self.start_time_j2000 = start_time
        self.end_time_j2000 = end_time
        self.spice_files = {}
        self.spice_gaps = {}
        self.allowed_spice_types = allowed_spice_types
        # Holds all files
        for type in allowed_spice_types:
            self.spice_files[type] = []
            self.spice_gaps[type] = [[start_time, end_time]]

        self.template_header = rf"""

       \begintext

       This is the most up to date Metakernel as of
       {datetime.now()}.

       This attempts to cover data from
       {self.start_time_j2000} to {self.end_time_j2000}
       seconds since J2000.

        """

    def load_spice(self, files, type):
        """Load the best SPICE files of a specific type into the Metakernel.

        Populates the self.spice_files dictionary with the best spice files.

        Parameters
        ----------
        files: dict
            dict
        type: str
            Tells that metakernel the type of files you are loading

        """
        if type not in self.allowed_spice_types:
            raise ValueError(
                f"Invalid type '{type}'. Allowed: {self.allowed_spice_types}"
            )
        spice_files_to_load = []
        gaps_remaining = []
        spice_files_reformatted = self._reformat_and_filter(files)
        for gap in self.spice_gaps[type]:
            gaps_remaining.extend(
                MetaKernel._find_best_files(
                    gap, spice_files_reformatted, spice_files_to_load
                )
            )
        self.spice_files[type].extend(spice_files_to_load)
        self._remove_duplicates_from_sorted_file_list(type)
        self.spice_gaps[type] = gaps_remaining

    def return_spice_files_in_order_detailed(self, order_to_load):
        # Returns the files (with all associated information) in the correct order to
        # be loaded in
        metakernel_files = []
        for type in order_to_load:
            if self.spice_files[type]:
                metakernel_files.extend(reversed(self.spice_files[type]))
        return metakernel_files

    def return_spice_files_in_order_truncated(
        self, base_path: Path, load_order: list[str]
    ):
        # Returns the files as a list of filenames, no longer than 80 characters
        metakernel_files = self.return_spice_files_in_order_detailed(load_order)
        filenames_to_return = []
        for f in metakernel_files:
            fn = base_path / f["file_name"]
            filename = self._limitstring(str(fn), 79, "+")
            filenames_to_return.extend(filename)
        return filenames_to_return

    def return_tm_file(self, base_path: Path, load_order: list[str]):
        # Returns the files as a Metakernel SPICE file
        filenames = self.return_spice_files_in_order_truncated(base_path, load_order)
        return self.template_header + self._generate_mk_body(kernelfiles=filenames)

    def _generate_mk_body(self, kernelfiles):
        kernel_lines = "',\n'".join(kernelfiles)
        kernel_lines = f"'{kernel_lines}'"
        lines = kernel_lines.splitlines()
        lines = [lines[0]] + [textwrap.indent(line, " " * 22) for line in lines[1:]]
        kernel_lines = "\n".join(lines)
        template_body = f"""
\\begindata

  KERNELS_TO_LOAD = ( {kernel_lines}
                    )

\\begintext
"""
        return template_body

    def _remove_duplicates_from_sorted_file_list(self, type: str):
        indicies_to_delete = []
        file_list = self.spice_files[type]
        for i in range(0, len(file_list)):
            if i in indicies_to_delete:
                continue
            logger.info(
                f"Searching for duplicates for file {file_list[i]['file_name']}"
            )
            for j in range(i + 1, len(file_list)):
                if file_list[i]["file_name"] == file_list[j]["file_name"]:
                    indicies_to_delete.append(j)
        for i in sorted(set(indicies_to_delete), reverse=True):
            del file_list[i]
        self.spice_files[type] = file_list

    def _reformat_and_filter(self, spice_items):
        """Reformat into a dict item with file_root as the key, instead of
        the file_name.

        As it is reformatting the dict, it goes through and filters out all of the
        "lesser" files.

        Returns
        -------
            {'file_root1': {file info 1 dict}, 'file_root2': {file info 2 dict}, etc}
        """
        file_dict = {}
        for _, file_info in spice_items.items():
            if file_info["file_root"] in file_dict:
                if file_info["version"] > file_dict[file_info["file_root"]]["version"]:
                    file_dict[file_info["file_root"]] = file_info
                else:
                    continue
            file_dict[file_info["file_root"]] = file_info
        return file_dict

    def _limitstring(self, dirstring, limit, sym):
        """Limits string based on a limit and adds a symbol to show that it has a
        continuation next line
        """
        results = []

        for i in range(0, len(dirstring), limit):
            string_segment = (
                dirstring[i : i + limit]
                if i + limit >= len(dirstring)
                else dirstring[i : i + limit] + sym
            )
            results.append(string_segment)
        return results

    @staticmethod
    def _find_best_files(trange, files_to_check, files_to_load):
        """This function finds the best files that cover the starting time range.
        This function is recursive, it finds the "best" file to load in, then
        calls itself again if gaps are still identified

        Returns a list of gaps in J2000 that cannot be found at all.
        """
        trange = [float(trange[0]), float(trange[1])]

        logger.info(f"Attempting to find file to cover {trange[0]!s} to {trange[1]!s}")
        gap_list = []
        return_gap_list = []
        # Find the "best" file to load in by latest date
        latest_delivery_date = -1
        best_file = None
        for file_root in files_to_check:
            if (files_to_check[file_root]["timestamp"] < latest_delivery_date) or (
                latest_delivery_date == -1
            ):
                latest_delivery_date = files_to_check[file_root]["timestamp"]
                best_file = files_to_check[file_root]

        # If there is no file found, return
        if best_file is None:
            return [trange]

        logger.info(f"Checking file {json.dumps(best_file)} as a possible inclusion")

        # Look for gaps in the time range that are not covered by the file
        add_to_list = False
        if (
            best_file["min_date_J2000"] <= trange[0]
            and best_file["max_date_J2000"] >= trange[1]
        ):
            add_to_list = True
        elif (
            best_file["min_date_J2000"] >= trange[0]
            and best_file["max_date_J2000"] <= trange[1]
        ):
            add_to_list = True
            gap_list.append([trange[0], best_file["min_date_J2000"]])
            gap_list.append([best_file["max_date_J2000"], trange[1]])
        elif (
            best_file["min_date_J2000"] >= trange[0]
            and best_file["min_date_J2000"] < trange[1]
        ):
            add_to_list = True
            gap_list.append([trange[0], best_file["min_date_J2000"]])
        elif (
            best_file["max_date_J2000"] > trange[0]
            and best_file["max_date_J2000"] <= trange[1]
        ):
            add_to_list = True
            gap_list.append([best_file["max_date_J2000"], trange[1]])
        else:
            logger.info(
                "File did not match the specified time range, file will not be loaded."
            )
            gap_list.append(trange)

        if add_to_list:
            # Look for gaps in the time range that are gaps with the file itself
            dont_load_file = False

            file_gaps = []
            if (
                len(best_file["file_intervals_J2000"]) > 1
            ):  # Implies there is gaps in the data
                previous_interval = None
                for interval in best_file["file_intervals_J2000"]:
                    if previous_interval is None:
                        previous_interval = interval
                    else:
                        file_gaps.append([previous_interval[1], interval[0]])
                        previous_interval = interval

            for g in file_gaps:
                if int(g[0]) <= trange[0] and int(g[1]) >= trange[1]:
                    # There is a gap in the range we are looking at! Try again!
                    logger.info(
                        "There is a gap in the specified time range, file will "
                        "not be loaded."
                    )
                    gap_list = [trange]
                    dont_load_file = True
                    continue
                elif int(g[0]) >= trange[0] and int(g[1]) <= trange[1]:
                    logger.info("There is a gap within the specified time range")
                    gap_list.append(g)
                elif int(g[0]) >= trange[0] and int(g[0]) <= trange[1]:
                    logger.info(
                        "There is a gap between the start of the gap and the end "
                        "of the time range"
                    )
                    gap_list.append([g[0], trange[1]])
                elif int(g[1]) >= trange[0] and int(g[1]) <= trange[1]:
                    logger.info(
                        "There is a gap between the start of the time range to "
                        "the end of the file gap"
                    )
                    gap_list.append([trange[0], g[1]])

            if not dont_load_file:
                logger.info("File was valid, adding to metakernal list.")
                files_to_load.append(best_file)
            else:
                logger.info(
                    "File did not cover time range, not adding to metakernal list."
                )

        # Already loaded or checked this file, remove from future function calls
        new_file_dict = dict(files_to_check)
        del new_file_dict[best_file["file_root"]]

        for g in gap_list:
            return_gap_list.extend(
                MetaKernel._find_best_files(g, new_file_dict, files_to_load)
            )

        return return_gap_list

    def __repr__(self):
        """Return all loaded SPICE files as JSON."""
        return json.dumps(self.return_spice_files_in_order_detailed())


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
        rendered_file = metakernel.return_tm_file(
            base_path=spice_directory,
            load_order=[
                "leapseconds",
                "planetary_constants",
                "frames",
                "spacecraft_clock",
                "planetary_ephemeris",
                "spacecraft_ephemeris",
                "spacecraft_attitude",
            ],
        )
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
        "min_date_J2000": file.min_date_j2000,
        "max_date_J2000": file.max_date_j2000,
        "file_intervals_J2000": file.file_intervals_j2000,
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
    start_time: int = 1000, end_time: int = 31525416070, type: str = ""
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

        results = session.execute(query).scalars().all()

        spice_file_dict = {}
        for n in results:
            spice_file_dict[n.file_name] = convert_spice_metadata_model_to_dict(n)

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
        metakernel.load_spice(static_spice_file, type)

    for ephem_type in [
        "ephemeris_reconstructed",
        "ephemeris_nominal",
        "ephemeris_predicted",
        "ephemeris_90days",
        "ephemeris_long",
        "ephemeris_launch",
    ]:
        if len(metakernel.spice_gaps["spacecraft_attitude"]) > 0:
            ephem_files = query_spice_metadata_database(
                start_time=start_time_j2000, end_time=end_time_j2000, type=ephem_type
            )
            metakernel.load_spice(ephem_files, "spacecraft_ephemeris")

    for attitude_type in ["attitude_history", "attitude_predict"]:
        if len(metakernel.spice_gaps["spacecraft_attitude"]) > 0:
            attitude_files = query_spice_metadata_database(
                start_time=start_time_j2000, end_time=end_time_j2000, type=attitude_type
            )
            metakernel.load_spice(attitude_files, "spacecraft_attitude")

    return metakernel
