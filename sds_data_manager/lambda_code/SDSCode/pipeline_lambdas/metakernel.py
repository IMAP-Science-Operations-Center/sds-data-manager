"""Contains a generic Metakernel Generator class."""

import logging
import json
import textwrap
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class MetaKernel:
    """Class for generating a metakernels from SPICE files."""

    def __init__(self, start_time: int, end_time: int, allowed_spice_types: list[str], 
                 min_gap_time: int = 0):
        """Initialize the Metakernel.

        Parameters
        ----------
        start_time: int
            The start_time in seconds after j2000
        end_time: int
            The end_time in seconds after j2000
        allowed_spice_types: list[str]
            A list of strings that represent the allowed types of SPICE files,
            in order of the priority with which to load them in the metakernel.
        min_gap_time: int
            The minimum gap time to ignore in seconds, and assume SPICE can 
            interpolate well enough over this small gap. 
        """
        self.minimum_gap_time_to_ignore = min_gap_time
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

    def load_spice(self, files: dict, type: str, priority_field: str):
        """Load the best SPICE files of a specific type into the Metakernel.

        This function will be called multiple times for each Metakernel to 
        add in files. The first files loaded in should ALWAYS contain a 
        higher priority than subsequent files.

        Subsequent calls to this function of the same type should always contain
        files with a LOWER priority. 
        
        For example, if you call "load_spice" with type="spacecraft_ephemeris", 
        first call "load_spice" with a list of high-priority kernels, such as 
        the final reconstructed kernels. After, you can call it with lower 
        priority kernels, such as long-term predicted ephemeris files.

        The result will be that the internal list of spice files and spice gaps
        will be updated with the newest information. But the initial gaps are 
        always filled by the files loaded in FIRST.

        Parameters
        ----------
        files: dict
            A dictionary of {'file1_name': {metadata1}, 'file2_name': {metadata2}}
            Required metadata fields are:
                file_name - The name of the file
                min_date_j2000 - The minimum date of data in the file
                max_date_j2000 - The maxmimum date of data in the file
                file_intervals_j2000 - A list of lists
                {priority} - A priority to help resolve conflicts within a single
                             load_spice() call. This can be anything that can be
                             compared with the ">" or "<" operators.
            Other items are allowed in the dictionary and will be returned.  
        type: str
            Tells that metakernel the type of files you are loading
        priority_field: str
            The field in the files dictionary to help this function determine the best
            file to cover the gap, in case of multiple matches.

        """
        if type not in self.allowed_spice_types:
            raise ValueError(
                f"Invalid type '{type}'. Allowed: {self.allowed_spice_types}"
            )
        spice_files_to_load = []
        gaps_remaining = []

        for gap in self.spice_gaps[type]:
            gaps_remaining.extend(
                self._find_best_files(
                    gap, files, spice_files_to_load, priority_field
                )
            )
        self.spice_files[type].extend(spice_files_to_load)
        self._remove_duplicates_from_sorted_file_list(type)
        self.spice_gaps[type] = gaps_remaining

    def return_spice_files_in_order_detailed(self) -> list[dict]:
        '''Return all SPICE files and their details.

        Loops through the self.spice_files dictionary and 
        returns them all as a list, in the order specified.

        Returns
        -------
        metakernel_files : list[dict]
            A list form of all the loaded files in order
        '''
        metakernel_files = []
        for type in self.allowed_spice_types:
            if self.spice_files[type]:
                metakernel_files.extend(reversed(self.spice_files[type]))
        return metakernel_files

    def return_tm_file(self, base_path: Path)->str:
        '''Generate a SPICE metakernel file from the self.spice_files

        Parameter
        ---------
        base_path: Path
            The path to the local SPICE directory
        
        Return
        ------
        metakernel: str
            A string of the entire contents of the metakernel
        '''
        MAXIMUM_LINE_LENGTH = 79
        metakernel_files = self.return_spice_files_in_order_detailed()
        kernelfiles = []
        for f in metakernel_files:
            fn = base_path / f["file_name"]
            filename = self._limitstring(str(fn), MAXIMUM_LINE_LENGTH, "+")
            kernelfiles.extend(filename)

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
        return self.template_header + template_body
    

    def _remove_duplicates_from_sorted_file_list(self, type: str):
        '''Remove any duplicate found in self.spice_files[type].
        
        Parameter
        ---------
        type: str
            The type of SPICE file to search search and remove duplicate 
            files from
        '''
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

    def _limitstring(self, dirstring, limit, sym):
        """Limits string based on a limit and adds a symbol to show that it has a
        continuation to the next line
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

    def _find_best_files(self, trange, files_to_check, files_to_load, priority_field:str):
        """Find the best file to cover a given "trange".

        This function is recursive, it finds the "best" file to load in, then
        calls itself again if there are still gaps identified. 

        Parameter
        ---------
        trange: list
            A 2-element list of start/end time
        files_to_check: dict
            The files to examine to potentially cover the gap in trange
        files_to_load: list
            The files that have been previously confirmed as necessary to cover
            other gaps in the file
        priority_field: str
            The dictionary field in files_to_check the represents the priority 
            of the file to load in some way. 

        Return
        ------
        return_gap_list: list[list[int, int]]
            A list of gaps that still remain uncovered
        """
        trange = [float(trange[0]), float(trange[1])]
        if (trange[1] - trange[0]) < self.minimum_gap_time_to_ignore:
            # Don't even bother if the gap is too small
            return []
        logger.info(f"Attempting to find file to cover {trange[0]!s} to {trange[1]!s}")
        gap_list = []
        return_gap_list = []
        # Find the "best" file to load in by latest date
        latest_priority = None
        best_file = None
        for file_name in files_to_check:
            if (latest_priority is None) or (files_to_check[file_name][priority_field] < latest_priority):
                latest_priority = files_to_check[file_name][priority_field]
                best_file = files_to_check[file_name]

        # If there is no file found, return
        if best_file is None:
            return [trange]

        logger.info(f"Checking file {json.dumps(best_file)} as a possible inclusion")

        # Look for gaps in the time range that are not covered by the file
        add_to_list = False
        if (
            best_file["min_date_j2000"] <= trange[0]
            and best_file["max_date_j2000"] >= trange[1]
        ):
            add_to_list = True
        elif (
            best_file["min_date_j2000"] >= trange[0]
            and best_file["max_date_j2000"] <= trange[1]
        ):
            add_to_list = True
            gap_list.append([trange[0], best_file["min_date_j2000"]])
            gap_list.append([best_file["max_date_j2000"], trange[1]])
        elif (
            best_file["min_date_j2000"] >= trange[0]
            and best_file["min_date_j2000"] < trange[1]
        ):
            add_to_list = True
            gap_list.append([trange[0], best_file["min_date_j2000"]])
        elif (
            best_file["max_date_j2000"] > trange[0]
            and best_file["max_date_j2000"] <= trange[1]
        ):
            add_to_list = True
            gap_list.append([best_file["max_date_j2000"], trange[1]])
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
                len(best_file["file_intervals_j2000"]) > 1
            ):  # Implies there is gaps in the data
                previous_interval = None
                for interval in best_file["file_intervals_j2000"]:
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
        del new_file_dict[best_file["file_name"]]

        for g in gap_list:
            return_gap_list.extend(
                self._find_best_files(g, new_file_dict, files_to_load, priority_field)
            )

        return return_gap_list

    def __repr__(self):
        """Return all loaded SPICE files as JSON."""
        return json.dumps(self.return_spice_files_in_order_detailed())