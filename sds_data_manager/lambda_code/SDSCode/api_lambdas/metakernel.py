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
        for spice_type in allowed_spice_types:
            self.spice_files[spice_type] = []
            self.spice_gaps[spice_type] = [(start_time, end_time)]

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
        spice_type: str,
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
        spice_type: str
            Tells that metakernel the type of files you are loading.
        file_intervals_field: str
            The field that contains the file intervals to sort on.
        priority_field: str
            (Optional) The field in the files dictionary to help this function
            determine the best file to cover the gap, in case of multiple matches.

        """
        if spice_type not in self.allowed_spice_types:
            raise ValueError(
                f"Invalid type '{spice_type}'. Allowed: {self.allowed_spice_types}"
            )

        # Sort the files in reverse order of priority
        # The "best" file to check will be at index 0, the "second best" at index 1, etc
        if priority_field:
            files.sort(key=lambda x: x[priority_field], reverse=True)

        # Check each file individually to determine if it should be added to the MK
        for f in files:
            self._check_file(f, spice_type, file_intervals_field)

    def _check_file(
        self, file_to_check: dict, spice_type: str, file_intervals_field: str
    ):
        """Determine if a given file should be added to the MK.

        This function takes in metadata about a file and checks the current
        self.spice_gaps for the given SPICE type. If any of the data intervals
        in the file cover any portion of the self.spice_gaps, then we add it to
        self.spice_files[type]. Then we reset the spice_gaps,
        given the data in this new file

        Parameter
        ---------

        file_to_check: dict
            A dictionary object describing the SPICE file to examine
        spice_type: str
            The type of SPICE file we are checking.
            For example, ephemeris, leapseconds, attitude, etc.
        file_intervals_field:
            The field in "file_to_check" that contains the time intervals in
            which the file has valid data.

        Return:
        ------
        None
            However, it can modify "self.spice_gaps[spice_type]"
            and "self.spice_files[spice_type]".


        Example:
        -------
        Suppose that
            >> self.spice_gaps[spice_type] = [(100,200)]
        This means we are missing data between 100 and 200.

        Now suppose we call this function with
            >> file_to_check = {file_intervals_field: [(1,140), (150,160)]}
        You can see that this file covers part of the gap, as it contains
        data between 100-140 and 150-160.

        So this function will now append "file_to_check" to self.spice_files[spice_type]

        Additionally, it should set the gaps to now be
            >> self.spice_gaps[spice_type] = [(140,150), (160,200)]
        Since these are the remaining time ranges that this file could not fill in.
        """
        # Simplest case - return if no gaps exist.
        if len(self.spice_gaps[spice_type]) == 0:
            return

        # This variable will contain all gaps that exist after checking this file
        new_gaps = []

        # Loop through all gaps.
        for gap in self.spice_gaps[spice_type]:
            if gap[1] - gap[0] < self.minimum_gap_time_to_ignore:
                # Ignore this gap if it is small enough
                continue

            # Before checking any further, do a preliminary check.
            # Does the maximum and minimum time in this file cover any
            # portion of this gap? If not, don't check each interval individually.
            gap_list = MetaKernel._calculate_gaps(
                [
                    [
                        file_to_check[file_intervals_field][0][0],
                        file_to_check[file_intervals_field][-1][1],
                    ]
                ],
                gap[0],
                gap[1],
            )

            if (
                len(gap_list) == 1
                and gap_list[0][0] == gap[0]
                and gap_list[0][1] == gap[1]
            ):
                # Since the gaps we calculate are the same as the initial gap, this file
                # *definitely* has no data that can span any of the remaining gaps.
                logger.debug(f"The file does not cover {gap} and will not be loaded.")
                new_gaps.extend([gap])  # Add the gap in; this file cannot fill it.
                continue

            # Now we calculate all gaps in the file
            subgap_list = MetaKernel._calculate_gaps(
                file_to_check[file_intervals_field], gap[0], gap[1]
            )

            # Now we loop through all gaps we calculated for this file
            # that are in the range (gap[0], gap[1]). We call them "subgaps".
            if (
                len(subgap_list) == 1
                and subgap_list[0][0] <= gap[0]
                and subgap_list[0][1] >= gap[1]
            ):
                # The initial gap still fully exists. We did not fill it in.
                logger.debug(f"File did not cover {gap}.")
                new_gaps.extend([gap])  # Add the gap in; this file cannot fill it.
            else:
                logger.debug(f"File filled in {gap}, adding to MK list.")

                # Check if we've already added it. No need to add it again.
                if file_to_check not in self.spice_files[spice_type]:
                    self.spice_files[spice_type].append(file_to_check)
                # Add any of these "subgaps" to the new list of gaps.
                new_gaps.extend(subgap_list)

        # Ensure no duplicated gaps exist by called "set".
        self.spice_gaps[spice_type] = list(set(new_gaps))

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
        for spice_type in self.allowed_spice_types:
            if self.spice_files[spice_type]:
                metakernel_files.extend(reversed(self.spice_files[spice_type]))
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
        for spice_type in self.spice_gaps:
            if len(self.spice_gaps[spice_type]) > 0:
                return True
        return False

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

    @staticmethod
    def _calculate_gaps(file_intervals, gap_start, gap_end):  # noqa: PLR0912
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
        remaining_gaps: list[tuple[Any, Any]]
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
                sub_gaps.extend([(start, file_interval_start)])  # Gaps before interval
            if file_interval_end < search_window_end:
                # ....--- search window --------------->
                # ....-- file coverage -------->
                if search_window_end > gap_end:
                    end = gap_end
                else:
                    end = search_window_end
                sub_gaps.extend([(file_interval_end, end)])  # Gaps after interval

        return sub_gaps

    def __repr__(self):
        """Return all loaded SPICE files as JSON."""
        return json.dumps(self.return_spice_files_in_order_detailed())
