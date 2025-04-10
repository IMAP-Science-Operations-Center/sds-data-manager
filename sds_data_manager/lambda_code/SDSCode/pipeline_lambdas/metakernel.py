"""Contains a generic Metakernel Generator class."""

import json
import logging
import textwrap
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class MetaKernel:
    """Class for generating a metakernels from SPICE files."""

    def __init__(
        self,
        start_time: int,
        end_time: int,
        allowed_spice_types: list[str],
        min_gap_time: int = 0,
    ):
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

    def load_spice(
        self,
        files: list[dict],
        type: str,
        file_intervals_field: str,
        priority_field: str = "",
    ):
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
        files: list[dict]
            A list of [{metadata1}, {metadata2}}
            Required metadata fields are:
                file_name - The name of the file
                {file_intervals} - A list of lists/tuples of 2 elements. The values
                                   can be anything that can be compared with the
                                   ">" or "<" operators.
                {priority} - An optionial priority to help resolve conflicts within
                             a single load_spice() call. This can be anything that can
                             be sorted.
            Other items are allowed in the dictionary and will be returned by the
            other Metakernel function calls.
        type: str
            Tells that metakernel the type of files you are loading.
        file_intervals_field: str
            The field that contains the file intervals to sort on.
        priority_field: str
            (Optional) The field in the files dictionary to help this function
            determine the best file to cover the gap, in case of multiple matches.

        """
        if type not in self.allowed_spice_types:
            raise ValueError(
                f"Invalid type '{type}'. Allowed: {self.allowed_spice_types}"
            )
        spice_files_to_load = []
        gaps_remaining = []
        if priority_field:
            files.sort(key=lambda x: x[priority_field], reverse=False)

        for gap in self.spice_gaps[type]:
            gaps_remaining.extend(
                self._find_best_files(
                    gap, files, spice_files_to_load, file_intervals_field
                )
            )
        self.spice_files[type].extend(spice_files_to_load)
        self._remove_duplicates_from_sorted_file_list(type)
        self.spice_gaps[type] = gaps_remaining

    def return_spice_files_in_order_detailed(self) -> list[dict]:
        """Return all SPICE files and their details.

        Loops through the self.spice_files dictionary and
        returns them all as a list, in the order specified.

        Returns
        -------
        metakernel_files : list[dict]
            A list form of all the loaded files in order
        """
        metakernel_files = []
        for type in self.allowed_spice_types:
            if self.spice_files[type]:
                metakernel_files.extend(reversed(self.spice_files[type]))
        return metakernel_files

    def return_tm_file(self, base_path: Path) -> str:
        """Generate a SPICE metakernel file from all loaded SPICE files.

        Parameter
        ---------
        base_path: Path
            The path to the local SPICE directory

        Return:
        ------
        metakernel: str
            A string of the entire contents of the metakernel
        """
        maximum_line_length = 79
        metakernel_files = self.return_spice_files_in_order_detailed()
        kernelfiles = []
        for f in metakernel_files:
            fn = base_path / f["file_name"]
            filename = self._limitstring(str(fn), maximum_line_length, "+")
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
        """Remove any duplicate found in self.spice_files[type].

        Parameter
        ---------
        type: str
            The type of SPICE file to search search and remove duplicate
            files from
        """
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
        """Limit a list of strings and add a '+' symbol."""
        results = []

        for i in range(0, len(dirstring), limit):
            string_segment = (
                dirstring[i : i + limit]
                if i + limit >= len(dirstring)
                else dirstring[i : i + limit] + sym
            )
            results.append(string_segment)
        return results

    def _find_best_files(
        self, trange, files_to_check, files_to_load, file_intervals_field
    ):
        """Find the best file to cover a given "trange".

        This function is recursive, it finds the "best" file to load in, then
        calls itself again if there are still gaps identified.

        Parameter
        ---------
        trange: list
            A 2-element list of start/end time
        files_to_check: list
            The files to examine to potentially cover the gap in trange,
            in order of priority
        files_to_load: list
            The files that have been previously confirmed as necessary to cover
            other gaps in the file
        file_intervals_field: str
            The key of the dictionary that represents the file intervals

        Return:
        ------
        return_gap_list: list[list[int, int]]
            A list of gaps that still remain uncovered
        """
        trange = [float(trange[0]), float(trange[1])]
        if (trange[1] - trange[0]) < self.minimum_gap_time_to_ignore:
            # Don't even bother if the gap is too small
            return []

        logger.info(f"Attempting to find file to cover {trange[0]!s} to {trange[1]!s}")

        if len(files_to_check) == 0:
            logger.info("No files left to check!")
            return [trange]
        logger.info(f"{type(files_to_check)}")
        best_file = files_to_check[-1]
        logger.info(f"Checking file {json.dumps(best_file)} as a possible inclusion")

        # Preliminary filter.
        # Does this file even have the *potential* for matching?
        gap_list = MetaKernel._calculate_gaps(
            [
                [
                    best_file[file_intervals_field][0][0],
                    best_file[file_intervals_field][-1][1],
                ]
            ],
            trange[0],
            trange[1],
        )
        if (
            len(gap_list) == 1
            and gap_list[0][0] == trange[0]
            and gap_list[0][1] == trange[1]
        ):
            logger.info(
                "The file does not cover our time range and will not be loaded."
            )
        else:
            logger.info(
                "The file start/end time is included in the time range we are "
                "looking for. Examining sub-gaps."
            )

            # Secondary filter: Do the gaps within this file create additional gaps?
            subgap_list = MetaKernel._calculate_gaps(
                best_file[file_intervals_field], trange[0], trange[1]
            )
            if (
                len(subgap_list) == 1
                and subgap_list[0][0] == trange[0]
                and subgap_list[0][1] == trange[1]
            ):
                logger.info(
                    "File did not cover time range, not adding to metakernal list."
                )
            elif not subgap_list:
                logger.info(
                    "File was valid, and no further gaps were found. "
                    "Adding to metakernal list."
                )
                files_to_load.append(best_file)
            else:
                logger.info(
                    "File was valid, though more gaps were found. "
                    "Adding to metakernal list."
                )
                files_to_load.append(best_file)
                gap_list.extend(subgap_list)

        # Now we've checked this file, remove from child function calls
        new_file_list = files_to_check.copy()
        new_file_list.pop()
        return_gap_list = []

        # If any more gaps remain, call this function again!
        for g in gap_list:
            return_gap_list.extend(
                self._find_best_files(
                    g, new_file_list, files_to_load, file_intervals_field
                )
            )

        return return_gap_list

    @staticmethod
    def _calculate_gaps(file_intervals, gap_start, gap_end):
        """Caclulate the gaps based on file_gaps.

        Parameters
        ----------
        file_intervals: list[list[Any, Any]]
            The intervals of a given spice kernel
        gap_start
            The start time of the data gap to look at
        gap_end
            The end time of the data gap to look at

        Return
        ------
        remaining_gaps: list[list[Any, Any]]
            The gaps definitely not covered by this file.
        """
        sub_gaps = []
        for i in range(0, len(file_intervals)):
            interval_start = file_intervals[i][0]
            interval_end = file_intervals[i][1]
            if i == 0:
                gap_start = max(gap_start, file_intervals[0][0])
            else:
                gap_start = file_intervals[i - 1][1]
            if i == len(file_intervals) - 1:
                gap_end = min(gap_end, file_intervals[-1][1])
            else:
                gap_end = file_intervals[i][1]

            if interval_start <= gap_start and interval_end >= gap_end:
                #      <------- gap range ------------>
                # <---------interval coverage ------------------>
                return []
            elif interval_start >= gap_start and interval_end <= gap_end:
                # <----------- gap range ----------------->
                #     <-----interval coverage ------>
                sub_gaps.extend([[gap_start, interval_start], [interval_end, gap_end]])
            elif interval_start >= gap_start and interval_start < gap_end:
                # <------- gap range ------------>
                #             <---------interval coverage ------------------>
                sub_gaps.extend([[gap_start, interval_start]])
            elif interval_end > gap_start and interval_end <= gap_end:
                #      <------- gap range ------------>
                # <---------interval coverage ------>
                sub_gaps.extend([[interval_end, gap_end]])
            else:
                # <----- gap range ---------->
                #                              <-----interval coverage ------>
                sub_gaps.extend([[gap_start, gap_end]])

        return sub_gaps

    def __repr__(self):
        """Return all loaded SPICE files as JSON."""
        return json.dumps(self.return_spice_files_in_order_detailed())
