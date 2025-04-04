
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import boto3
import spiceypy
from imap_data_access import SPICEFilePath
from sqlalchemy.dialects.postgresql import insert

from ..database import database as db
from ..database import models
import glob
import json
import spiceypy
import math
from sqlalchemy import select
import textwrap

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


SPACECRAFT_ID = -43


def generate_mk(year):
    '''
    This function will determine a new metakernel file name and determine the contents of the file
    '''
    spice_mount_path = Path(os.getenv("EFS_SPICE_MOUNT_PATH"))  # Eg. /mnt/spice
    # Query for most recent MK file in the current year
    logger.info('Checking for existing metakernels in %s', year)

    # Take a look at the directory directly
    sorted_mks = sorted(glob.glob(f"{str(spice_mount_path)}/mk/*{year}*"))

    if len(sorted_mks) > 0:
        most_recent_mk = os.path.basename(sorted_mks[-1])
    else:
        most_recent_mk = None

    # Determine version number to use
    if most_recent_mk:
        logger.info('Metakernel exists for %s', year)
        metakernel_info = SPICEFilePath(most_recent_mk)
        most_recent_ver = int(metakernel_info.spice_metadata['version'])
        # Tick up the version number
        most_recent_ver += 1
        most_recent_ver = str(most_recent_ver).zfill(3)
    else:
        logger.info('No metakernal exists for %s, creating one now', year)
        most_recent_ver = str(1).zfill(3)

    # Determine file name
    mk_filename = f'imap_{year}_v{most_recent_ver}.tm'

    # Create a SPICE metakernel
    MK = create_imap_metakernel(start_time='{}-01-01'.format(year),
                                end_time='{}-01-01'.format(year + 1),
                                time_units='datetime')
    if MK is not None:
        rendered_file = MK.return_tm_file()
        logger.info('Rendered new metakernel %s', rendered_file)
    else:
        rendered_file = None

    #if most_recent_mk is not None and rendered_file is not None:
    #    # Check if identical to previous version, if so, do not create a new one.
    #    with open(f"{config.spice_directory}/metakernel/{most_recent_mk}", "r") as f:
    #        most_recent_mk_contents = f.read()

        # Ignore the first 100 characters of the file, that contains information about the date that the metakernel was generated
    #    logger.info(f"Old file: {most_recent_mk_contents[110:]}")
    #    logger.info(f"New file: {rendered_file[110:]}")

    #    if most_recent_mk_contents[110:] == rendered_file[110:]:
    #        logger.info("New SPICE file is identical to old SPICE file, continuing without generating new Metakernel.")
    #        return None, None

    return mk_filename, rendered_file

def create_new_metakernel():
    # Create up to date metakernels for various years of the mission
    for yr in range(2023, datetime.utcnow().year + 1):
        mk_file, rendered_file = generate_mk(yr)
        if rendered_file is not None:
            print(rendered_file)

def convert_spice_metadata_model_to_dict(file):
    spice_file_dict = {}
    spice_file_dict['file_name'] = file.file_name
    spice_file_dict['file_root'] = file.file_root
    spice_file_dict['kernel_type'] = file.kernel_type
    spice_file_dict['version'] = file.version
    spice_file_dict['min_date_J2000'] = file.min_date_j2000
    spice_file_dict['max_date_J2000'] = file.max_date_j2000
    spice_file_dict['file_intervals_J2000'] = file.file_intervals_j2000
    spice_file_dict['min_date_datetime'] = file.min_date_datetime.strftime("%Y-%m-%d, %H:%M:%S")
    spice_file_dict['max_date_datetime'] = file.max_date_datetime.strftime("%Y-%m-%d, %H:%M:%S")
    spice_file_dict['min_date_sclk'] = file.min_date_sclk
    spice_file_dict['max_date_sclk'] = file.max_date_sclk
    spice_file_dict['file_intervals_sclk'] = file.file_intervals_sclk
    spice_file_dict['sclk_kernel'] = file.sclk_kernel
    spice_file_dict['lsk_kernel'] = file.lsk_kernel
    spice_file_dict['ingestion_date'] = file.ingestion_date.strftime("%Y-%m-%d, %H:%M:%S")
    spice_file_dict['timestamp'] = file.ingestion_date.timestamp()

    return spice_file_dict

def query_spice_metadata_database(start_time=1000, end_time=31525416070, type=None):

    with db.Session() as session, session.begin():
        query = select(models.SPICEFiles)  

        query = query.where(models.SPICEFiles.min_date_j2000 <= end_time)
        query = query.where(models.SPICEFiles.max_date_j2000 >= start_time)

        if type:
            query = query.where(models.SPICEFiles.kernel_type == type)

        results = session.execute(query).scalars().all()

        spice_file_dict = {}
        for row in results:
            print(row.kernel_type, row.version, row.file_name, row.min_date_j2000, row.max_date_j2000)
        for n in results:
            print(n.file_name)
            spice_file_dict[n.file_name] = convert_spice_metadata_model_to_dict(n)

        return spice_file_dict

def convert_to_j2000(time, units):
    if units == 'sclk':
        return spiceypy.scs2e(SPACECRAFT_ID, time)
    if units == 'datetime':
        return spiceypy.datetime2et(datetime.strptime(time, "%Y-%m-%d"))

def create_imap_metakernel(start_time=10000,
                          end_time=31525416070,
                          time_units='j2000'):
    '''
    The following creates a MetaKernel class and inserts files into it
    '''

    if time_units != 'j2000':
        start_time = convert_to_j2000(start_time, time_units)
        end_time = convert_to_j2000(end_time, time_units)
        logger.info(f"Converted {start_time} and {end_time} to J2000")

    start_time = math.floor(float(start_time))
    end_time = math.ceil(float(end_time))

    # Create the Metakernel class
    MK = MetaKernel(start_time, end_time)

    static_files_load_order = ["leapseconds",
                               "planetary_constants",
                               "frames",
                               "spacecraft_clock",
                               "planetary_ephemeris"]

    logger.info("Loading static files")
    for type in static_files_load_order:
        static_spice_file = query_spice_metadata_database(type=type)
        MK.load_static_files(static_spice_file)

    for ephem_type in ['ephemeris_reconstructed', 'ephemeris_nominal', 'ephemeris_predicted', 'ephemeris_90days', 'ephemeris_long', 'ephemeris_launch']:
        if len(MK.gaps_in_ephemeris_data) > 0:
            print("######################################################################")
            print(f"Checking {ephem_type}")
            ephem_files = query_spice_metadata_database(start_time=start_time, end_time=end_time, type=ephem_type)
            MK.load_ephemeris(ephem_files)

    for attitude_type in ['attitude_history', 'attitude_predict']:
        if len(MK.gaps_in_attitude_data) > 0:
            attitude_files = query_spice_metadata_database(start_time=start_time, end_time=end_time, type=attitude_type)
            MK.load_attitude(attitude_files)

    return MK

class MetaKernel:
    '''
    This is a class for generating a metakernel from SPICE files.

    First, initialize the MK with a time span: x = MetaKernel(date1,date2)
    Then, load in the files.  Start with "load_final_ephemeris" or "load_final_attitude",
    followed by "load_reconstructed_ephemeris/attitude", and then "load_predicted_ephemeris/attitude".

    For each step above, this class will automatically find the files that provide the maximum coverage, while getting
    rid of any redundant files.

    All input must be a list of dictionary objects.  The fields MUST include:
        file_name - the name of the file
        file_root - the name of the file without version information
        min_date_J2000 - the minimum time in the file
        max_date_J2000 - the maximum time in the file
        file_intervals_J2000 - the intervals over which there is data.  This is the output of ckcov and spkcov
        version - the version info of the file (just a number).  Higher version has precidence.
        OD - similar to version info, this is the orbital determination number.  Higher OD has precidence
        timestamp - the timestamp of the file.  Later timestamps have precidence.
    '''

    def __init__(self, start_time, end_time):

        # Gaps smaller than this are ignored and assumed that SPICE can interpolate between
        self.MIN_GAP_TIME_TO_INGORE = 3  # Seconds

        self.start_time_j2000 = start_time
        self.end_time_j2000 = end_time

        # Holds all gaps in the files
        self.gaps_in_ephemeris_data = [[start_time, end_time]]
        self.gaps_in_attitude_data = [[start_time, end_time]]

        # Holds all files
        self.static_files = []
        self.ephemeris_files = []
        self.attitude_files = []

        self.template_header = fr'''

       \begintext

       This is the most up to date IMAP Metakernel as of {datetime.now()}.

       This attempts to cover data from {self.start_time_j2000} to {self.end_time_j2000} seconds since J2000.

        '''

    def generate_mk_body(self, kernelfiles):

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

    def load_static_files(self, static_files):
        best_version = -1
        file_to_load = None
        for file_name, file_info in static_files.items():
            if file_info['version'] > best_version:
                best_version = file_info['version']
                file_to_load = file_name
        if file_to_load is not None:
            self.static_files.append(static_files[file_to_load])
        else:
            return

    def load_ephemeris(self, ephem_files):
        ephem_files_to_load = []
        gaps_remaining = []

        ephem_reformatted = self._reformat_and_filter(ephem_files)

        for gap in self.gaps_in_ephemeris_data:
            gaps_remaining.extend(self._find_best_files(gap, ephem_reformatted, ephem_files_to_load))

        self.ephemeris_files.extend(ephem_files_to_load)
        self.ephemeris_files = self._remove_duplicates_from_sorted_file_list(self.ephemeris_files)
        self.gaps_in_ephemeris_data = gaps_remaining
        return

    def load_attitude(self, attitude_files):
        attitude_files_to_load = []
        gaps_remaining = []

        attitude_reformatted = self._reformat_and_filter(attitude_files)

        for gap in self.gaps_in_attitude_data:
            gaps_remaining.extend(self._find_best_files(gap, attitude_reformatted, attitude_files_to_load))

        self.attitude_files.extend(attitude_files_to_load)
        self.attitude = self._remove_duplicates_from_sorted_file_list(self.attitude_files)
        self.gaps_in_attitude_data = gaps_remaining
        return

    def return_spice_files_in_order_detailed(self):
        # Returns the files (with all associated information) in the correct order to be loaded in
        metakernel_files = []
        if self.static_files:
            metakernel_files.extend(reversed(self.static_files))
        if self.ephemeris_files:
            metakernel_files.extend(reversed(self.ephemeris_files))
        if self.attitude_files: 
            metakernel_files.extend(reversed(self.attitude_files))

        return metakernel_files

    def return_spice_files_in_order_truncated(self, base_path=''):
        # Returns the files as a list of filenames, no longer than 80 characters
        metakernel_files = self.return_spice_files_in_order_detailed()
        filenames_to_return = []
        for f in metakernel_files:
            fn = base_path + f['file_name']
            filename = self._limitstring(fn, 79, '+')
            filenames_to_return.extend(filename)
        return filenames_to_return

    def return_tm_file(self, base_path=''):
        # Returns the files as a Metakernel SPICE file
        filenames = self.return_spice_files_in_order_truncated(base_path)
        return self.template_header + self.generate_mk_body(kernelfiles=filenames)

    def _remove_duplicates_from_sorted_file_list(self, file_list):
        indicies_to_delete = []
        for i in range(0, len(file_list)):
            if i in indicies_to_delete:
                continue
            logger.info(f"Searching for duplicates for file {file_list[i]['file_name']}")
            for j in range(i+1, len(file_list)):
                if file_list[i]['file_name'] == file_list[j]['file_name']:
                    indicies_to_delete.append(j)
        for i in sorted(set(indicies_to_delete), reverse=True):
            del file_list[i]
        return file_list

    def _reformat_and_filter(self, spice_items):
        # Reformat into a dict item with file_root as the key, instead of the file_name.
        # As it is reformatting the dict, it goes through and filters out all of the "lesser" files.
        # Returns: {'file_root1': {file info 1 dictionary}, 'file_root2': {file info 2 dictionary}, etc}
        file_dict = {}
        for _, file_info in spice_items.items():
            if file_info['file_root'] in file_dict:
                if file_info['version'] > file_dict[file_info['file_root']]['version']:
                    file_dict[file_info['file_root']] = file_info
                else:
                    continue
            file_dict[file_info['file_root']] = file_info
        return file_dict

    def _limitstring(self, dirstring, limit, sym):
        """limits string based on a limit and adds a symbol to show that it has a continuation next line
        """
        results = []

        for i in range(0, len(dirstring), limit):
            string_segment = dirstring[i:i + limit] if i + limit >= len(dirstring) else dirstring[i:i + limit] + sym
            results.append(string_segment)
        return results

    def _find_best_files(self, trange, files_to_check, files_to_load):
        '''
        This function finds the best files that cover the starting time range.
        This function is recursive, it finds the "best" file to load in, then calls itself again if gaps are still identified
        Returns a list of gaps in J2000 that cannot be found at all.
        '''
        trange = [float(trange[0]), float(trange[1])]
        if (trange[1] - trange[0]) < self.MIN_GAP_TIME_TO_INGORE:
            # Don't even bother if the gap is too small
            return []

        logger.info(f"Attempting to find file to cover {str(trange[0])} to {str(trange[1])}")
        gap_list = []
        return_gap_list = []
        # Find the "best" file to load in by latest date
        latest_delivery_date = -1
        best_file = None
        for file_root in files_to_check:
            if (files_to_check[file_root]['timestamp'] < latest_delivery_date) or (latest_delivery_date == -1):
                latest_delivery_date = files_to_check[file_root]['timestamp']
                best_file = files_to_check[file_root]

        # If there is no file found, return
        if best_file is None:
            return [trange]

        logger.info(f"Checking file {json.dumps(best_file)} as a possible inclusion to the metakernel")

        # Look for gaps in the time range that are not covered by the file
        add_to_list = False
        if best_file['min_date_J2000'] <= trange[0] and best_file['max_date_J2000'] >= trange[1]:
            add_to_list = True
        elif best_file['min_date_J2000'] >= trange[0] and best_file['max_date_J2000'] <= trange[1]:
            add_to_list = True
            gap_list.append([trange[0], best_file['min_date_J2000']])
            gap_list.append([best_file['max_date_J2000'], trange[1]])
        elif best_file['min_date_J2000'] >= trange[0] and best_file['min_date_J2000'] < trange[1]:
            add_to_list = True
            gap_list.append([trange[0], best_file['min_date_J2000']])
        elif best_file['max_date_J2000'] > trange[0] and best_file['max_date_J2000'] <= trange[1]:
            add_to_list = True
            gap_list.append([best_file['max_date_J2000'], trange[1]])
        else:
            logger.info("File did not match the specified time range, file will not be loaded.")
            gap_list.append(trange)

        if add_to_list:
            # Look for gaps in the time range that are gaps with the file itself
            dont_load_file = False

            file_gaps = []
            if len(best_file['file_intervals_J2000']) > 1:  # Implies there is gaps in the data
                previous_interval = None
                for interval in best_file['file_intervals_J2000']:
                    if previous_interval == None:
                        previous_interval = interval
                    else:
                        file_gaps.append([previous_interval[1], interval[0]])
                        previous_interval = interval

            for g in file_gaps:
                if int(g[0]) <= trange[0] and int(g[1]) >= trange[1]:
                    # There is a gap in the range we are looking at! Try again!
                    logger.info("There is a gap in the specified time range, file will not be loaded.")
                    gap_list = [trange]
                    dont_load_file = True
                    continue
                elif int(g[0]) >= trange[0] and int(g[1]) <= trange[1]:
                    logger.info("There is a gap within the specified time range")
                    gap_list.append(g)
                elif int(g[0]) >= trange[0] and int(g[0]) <= trange[1]:
                    logger.info("There is a gap between the start of the gap and the end of the time range")
                    gap_list.append([g[0], trange[1]])
                elif int(g[1]) >= trange[0] and int(g[1]) <= trange[1]:
                    logger.info("There is a gap between the start of the time range to the end of the file gap")
                    gap_list.append([trange[0], g[1]])

            if not dont_load_file:
                logger.info("File was valid, adding to metakernal list.")
                files_to_load.append(best_file)
            else:
                logger.info("File did not cover time range, not adding to metakernal list.")

        # Already loaded or checked this file, remove from future function calls
        new_file_dict = dict(files_to_check)
        del new_file_dict[best_file['file_root']]

        for g in gap_list:
            return_gap_list.extend(self._find_best_files(g, new_file_dict, files_to_load))

        return return_gap_list

    def __repr__(self):
        return json.dumps(self.return_spice_files_in_order_detailed())