"""Contains a generic Metakernel Generator class."""

import json
import logging
import textwrap
from datetime import datetime, timezone
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

        self.template_header = f"""
\\begintext

This is the most up to date Metakernel as of
{datetime.now(timezone.utc)}.

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
        if priority_field:
            spice_files_to_load.sort(key=lambda x: x[priority_field], reverse=True)
        self.spice_files[type].extend(spice_files_to_load)
        self._remove_duplicates_from_sorted_file_list(type)
        self.spice_gaps[type] = gaps_remaining

    def return_spice_files_in_order(self, detailed: bool = True) -> list[dict]:
        """Return all SPICE files and their details.

        Loops through the self.spice_files dictionary and
        returns them all as a list, in the order specified.

        Parameter
        ---------
        detailed : bool
            If true, returns all information about the file.
            If false, returns only the file names themselves.

        Returns
        -------
        metakernel_files : list[dict]
            A list form of all the loaded files in order
        """
        metakernel_files = []
        for type in self.allowed_spice_types:
            if self.spice_files[type]:
                metakernel_files.extend(reversed(self.spice_files[type]))
        if detailed:
            return metakernel_files
        else:
            return [f["file_name"] for f in metakernel_files]

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
        metakernel_files = self.return_spice_files_in_order(detailed=False)
        kernelfiles = []
        for f in metakernel_files:
            fn = base_path / f
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

    def contains_gaps(self):
        """Determine if there are gaps that remain to be filled."""
        for type in self.spice_gaps:
            if len(self.spice_gaps[type]) > 0:
                return True
        return False

    def _remove_duplicates_from_sorted_file_list(self, type: str):
        """Remove any duplicate found in self.spice_files[type].

           Loops through the list, and determines which files to
           keep.

        Parameter
        ---------
        type: str
            The type of SPICE file to search search and remove duplicate
            files from
        """
        old_file_list = self.spice_files[type].copy()
        file_names_to_keep = []
        new_file_list = []
        for i in range(0, len(old_file_list)):
            if old_file_list[i]["file_name"] in file_names_to_keep:
                continue
            file_names_to_keep.append(old_file_list[i]["file_name"])
            new_file_list.append(old_file_list[i])

        self.spice_files[type] = new_file_list

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
        trange = [trange[0], trange[1]]
        if (trange[1] - trange[0]) < self.minimum_gap_time_to_ignore:
            # Don't even bother if the gap is too small
            return []

        logger.debug(f"Attempting to find file to cover {trange[0]!s} to {trange[1]!s}")

        if len(files_to_check) == 0:
            logger.debug("No files left to check!")
            return [trange]

        best_file = files_to_check[-1]
        logger.debug(f"Checking file {best_file['file_name']} as a possible inclusion")

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
            logger.debug(
                "The file does not cover our time range and will not be loaded."
            )
            subgap_list = gap_list
        else:
            logger.debug(
                "The file start/end time is included in the time range we are "
                "looking for. Examining sub-gaps."
            )

            # Secondary filter: Do the gaps within this file create additional gaps?
            subgap_list = MetaKernel._calculate_gaps(
                best_file[file_intervals_field], trange[0], trange[1]
            )
            if (
                len(subgap_list) == 1
                and subgap_list[0][0] <= trange[0]
                and subgap_list[0][1] >= trange[1]
            ):
                logger.debug(
                    "File did not cover time range, not adding to metakernal list."
                )
                subgap_list = [trange]
            elif not subgap_list:
                logger.debug(
                    "File was valid, and no further gaps were found. "
                    "Adding to metakernal list."
                )
                files_to_load.append(best_file)
            else:
                logger.debug(
                    "File was valid, though more gaps were found. "
                    "Adding to metakernal list."
                )
                files_to_load.append(best_file)

        # Now we've checked this file, remove from child function calls
        new_file_list = files_to_check.copy()
        new_file_list.pop()
        return_gap_list = []
        # If any more gaps remain, call this function again!

        for g in subgap_list:
            return_gap_list.extend(
                self._find_best_files(
                    g, new_file_list, files_to_load, file_intervals_field
                )
            )
        return return_gap_list

    @staticmethod
    def _calculate_gaps(file_intervals, gap_start, gap_end):
        """Caclulate the gaps based on file_intervals.

        Slide a "window" across the file to determine the intervals
        that remain uncovered between gap_start and gap_end.

        A visual representation:

        gap start                                                            gap end
        |-------------------------------------------------------------------------|

        The valid intervals in the file:
          |----------|   |------------------|   |--------------------------|
           interval 1         interval 2                interval 3

        The calculated search windows:
        |----------------|----------------------|---------------------------------|
        search window 1     search window 2               search window 3

        The calculated gaps:
        |-|          |---|                   |--|                           |-----|
        gap 1        gap 2                   gap 3                           gap 4

        This function then returns this list of calculated gap intervals

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
            file_interval_start = file_intervals[i][0]
            file_interval_end = file_intervals[i][1]

            # Determine the search window
            if (
                file_interval_start <= gap_start and file_interval_end >= gap_start
            ) or i == 0:
                search_window_start = gap_start
            else:
                search_window_start = file_intervals[i - 1][1]
            if (
                file_interval_start <= gap_end and file_interval_end >= gap_end
            ) or i == len(file_intervals) - 1:
                search_window_end = gap_end
            else:
                search_window_end = file_intervals[i][1]

            # Quick check, are we out of bounds of the range we care about?
            # <---- search window ----->
            #                               <----- gap span ------>
            if search_window_start >= gap_end or search_window_end <= gap_start:
                continue

            # Another quick check, does this already interval cover everything
            # we're looking for?
            #      <------- gap span ------------>
            # <--------- file coverage -------------------->
            if file_interval_start <= gap_start and file_interval_end >= gap_end:
                return []  # Return here, no gaps to needed to fill

            # Calculate and append gaps to the list
            if file_interval_start > search_window_start:
                # <----------- search window --------....
                #       <----- file coverage --------....
                if search_window_start < gap_start:
                    start = gap_start
                else:
                    start = search_window_start
                sub_gaps.extend(
                    [[start, file_interval_start]]
                )  # Gaps before interval
            if file_interval_end < search_window_end:
                # ....--- search window --------------->
                # ....-- file coverage -------->
                if search_window_end > gap_end:
                    end = gap_end
                else:
                    end = search_window_end
                sub_gaps.extend(
                    [[file_interval_end, end]]
                )  # Gaps after interval

        return sub_gaps

    def __repr__(self):
        """Return all loaded SPICE files as JSON."""
        return json.dumps(self.return_spice_files_in_order_detailed())
