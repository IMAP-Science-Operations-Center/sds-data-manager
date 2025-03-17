"""Functions to write SPICE ingested files to EFS."""

import logging
import os
from pathlib import Path
from . import spice_utilities
import boto3
from ..database import database as db
from ..database import models

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Define the paths
spice_mount_path = Path(os.getenv("EFS_SPICE_MOUNT_PATH"))  # Eg. /mnt/spice

#Covers:
# Historical Attitude (type: ah.bc)
# Predicted Attitude (type: ap.bc)
attitude_file_pattern = (r'(?P<{0}>imap)_'
                           r'(?P<{1}>[\d]{{4}})_'
                           r'(?P<{2}>[\d]{{3}})_'
                           r'(?P<{3}>[\d]{{4}})_'
                           r'(?P<{4}>[\d]{{5}})_'
                           r'(?P<{5}>[a-zA-Z0-9\-_]+)\.'
                           r'(?P<{6}>ah.bc|ap.bc)').format('mission',
                                                           'year_start', 
                                                           'doy_start',
                                                           'year_end',
                                                           'doy_end',
                                                           'version',
                                                           'type')
attitude_file_regex = re.compile(attitude_file_pattern)

#Covers:
# Reconstructed (type: recon)
# Nominal (type: nom)
# Predict (type: pred)
# 90 Day Predict (type: 90days)
# Long Term Predict (type: long)
# Launch Predict (type: launch)
spacecraft_ephemeris_file_pattern = (r'(?P<{0}>imap)_'
                                    r'(?P<{1}>[a-zA-Z0-9\-]+)_'
                                    r'(?P<{2}>[\d]{{8}})_'
                                    r'(?P<{3}>[\d]{{8}})_'
                                    r'(?:|_v(?P<{4}>[\d]*))\.'
                                    r'(?P<{5}>bsp)').format('mission',
                                                            'type',
                                                            'date_start',
                                                            'date_end',
                                                            'version',
                                                            'extension')
spacecraft_ephemeris_regex = re.compile(spacecraft_ephemeris_file_pattern)

#Covers: 
# Planetary Ephemeris (type: "de")
# Planetary Constants (type: "pck")
# Leapsecond kernel (type: "naif")
# Spacecraft clock kernel (type: "imapsclk_")
spice_prod_ver_pattern = (r'(?P<{0}>[a-zA-Z\-_]+)'
                          r'(?P<{1}>[\d]+)\.'
                          r'(?P<{2}>tls|tpc|bsp|tsc)').format('type',
                                                              'version',
                                                              'extension')
spice_prod_ver_regex = re.compile(spice_prod_ver_pattern)

#Covers:
# Frame: (type: 'tf')
spice_frame_pattern = (r'(?P<{0}>imap)_'
                            r'(?P<{2}>[\d]+)\.'
                            r'(?P<{3}>tf)').format('mission',
                                                   'version',
                                                   'type')
spice_frame_regex = re.compile(spice_frame_pattern)

mk_filename_pattern = (r'(?P<{0}>imap)_'
                           r'(?P<{1}>[\d]{{4}})_'
                           r'v(?P<{2}>[\d]{{3}})\.'
                           r'(?P<{3}>tm)').format('mission',
                                                  'year',
                                                  'version',
                                                  'type')

mk_filename_regex = re.compile(emm_mk_filename_pattern)


def generate_mk(year):
    '''
    This function will determine a new metakernel file name and determine the contents of the file
    '''

    # Query for most recent MK file in the current year
    logger.info('Checking for existing metakernels in %s', year)

    # Take a look at the directory directly, instead of going through the database
    sorted_mks = sorted(glob.glob(f"{str(spice_mount_path)}/mk/*{year}*"))

    if len(sorted_mks) > 0:
        most_recent_mk = os.path.basename(sorted_mks[-1])
    else:
        most_recent_mk = None

    # Determine version number to use
    if most_recent_mk:
        logger.info('Metakernel exists for %s', year)
        version_extract = extract_parts([mk_filename_regex], most_recent_mk,
                                                     ['version'])

        most_recent_ver = int(version_extract['version'])

        # Tick up the version number
        most_recent_ver += 1
        most_recent_ver = str(most_recent_ver).zfill(3)
    else:
        logger.info('No metakernal exists for %s, creating one now', year)
        most_recent_ver = str(1).zfill(3)

    # Determine file name
    mk_filename = f'imap_{year}_v{most_recent_ver}.tm'

    # Create a SPICE metakernel
    MK = spice_utilities.create_imap_metakernel(start_time='{}-01-01'.format(year),
                                               end_time='{}-01-01'.format(year + 1),
                                               time_units='datetime',
                                               sclk_kernel=get_latest_sclk(),
                                               lsk_kernel=get_latest_lsk())
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
    # Step 9: Create up to date metakernels for various years of the mission
    for yr in range(2024, datetime.utcnow().year + 1):
        mk_file, rendered_file = generate_mk(yr)
        if rendered_file is not None:
            print(rendered_file)

def extract_parts(regex_list, string_to_parse, parts, transforms=None, group_regexes=None, handle_missing_parts=False):
    '''
    Method used to extract the groups provided in order or None if no provided regular expressions match
    Arguments:
        regex_list - A list of compiled regular expressions to be applied in order
        string_to_parse - The string to check against the provided regular expressions
        parts - A list of groups to be extracted
        transforms - A dictionary of group->function (that takes 1 string) to transform the string into some other type
        group_regexes - A list of tuples that contain the group to check as well as the compiled regular expression to check against.
            This is used to further refine the regular expression search.
        handle_missing_parts - If True, missing parts won't raise an exception and the result set will contain None
                              for the missing part.  If False, an IndexError is raised
        Returns:
            A tuple of the groups that were requested in parts or None if there were no matches for the provided regex_list
    '''

    m = matches_on_group(regex_list, string_to_parse, group_regexes)
    if m is None:
        return None

    ret_val = OrderedDict()
    for part in parts:
        try:
            if transforms is not None and part in transforms:
                ret_val[part] = transforms[part](m.group(part))
            else:
                ret_val[part] = m.group(part)
        except IndexError as ie:
            if handle_missing_parts:
                if transforms is not None and part in transforms:
                    ret_val[part] = transforms[part](None)
                else:
                    ret_val[part] = None
            else:
                logger.warning('Group %s does not exist!', part)
                raise ie

    return ret_val

def get_latest_sclk():
    sclk_sorted = sorted(os.listdir(f"{str(spice_mount_path)}/sclk"))
    if sclk_sorted :
        return str(spice_mount_path) + "/sclk/" + sclk_sorted[-1]

def get_latest_lsk():
    lsk_sorted = sorted(os.listdir(f"{str(spice_mount_path)}/lsk"))
    if lsk_sorted:
        return str(spice_mount_path) + "/lsk/" + lsk_sorted[-1]

def get_coverage_dictionary(files_to_load, latest_sclk, latest_lsk):
    '''
    Returns a dictionary of a list of lists.  For example:
    {
     'file1': [[interval1_start, interval1_end], [interval2_start, interval2_end]],
     'file2': [[interval3_start, interval3_end]]
    }
    '''
    result = {}
    try:
        spiceypy.furnsh(latest_sclk)
        spiceypy.furnsh(latest_lsk)
    except:
        logger.warning("Unable to load spacecraft clock and/or leapseconds kernel.  "
                       "Returning empty values for file coverage.")
        for f in files_to_load:
            result[f] = []
        return result

    for f in files_to_load:
        file_root, file_ext = os.path.splitext(f)
        if "mk" in file_ext:
            continue
        result[f] = []
        if "de440.bsp" in f:
            result[f].append([spiceypy.datetime2et(mission_start_time), spiceypy.datetime2et(mission_end_time)])
            continue
        if file_ext == ".bc":
            try:
                cover = spiceypy.cell_double(10000)
                cover = spiceypy.ckcov(f, SPACECRAFT_ID*1000, False, 'INTERVAL', 0.0, 'TDB', cover)
                card = spiceypy.wncard(cover)
                for i_window in range(card):
                    (left, right) = spiceypy.wnfetd(cover, i_window)
                    result[f].append([left, right])
            except Exception as e:
                logger.warning(f"Unable to gather coverage information from file {f} due to error {str(e)}.  Removing from files to be indexed.  ")
                del result[f]
                continue
        elif file_ext == ".bsp":
            try:
                cover = spiceypy.cell_double(10000)
                cover = spiceypy.spkcov(f, SPACECRAFT_ID, cover)
                card = spiceypy.wncard(cover)
                for i_window in range(card):
                    (left, right) = spiceypy.wnfetd(cover, i_window)
                    result[f].append([left, right])
            except Exception as e:
                logger.warning(f"Unable to gather coverage information from file {f} due to error {str(e)}.  Removing from files to be indexed.  ")
                del result[f]
                continue

        else:
            result[f].append([spiceypy.datetime2et(mission_start_time), spiceypy.datetime2et(mission_end_time)])

        if result[f] == []:
            result[f].append([spiceypy.datetime2et(mission_start_time), spiceypy.datetime2et(mission_end_time)])

    return result

def file_coverage_list_to_datetime_strings(file_coverage_list):
    '''
    Returns a new file coverage dictionary that contains the datetime instead of seconds since J2000 epoch
    '''
    new_dict = {}
    for f in file_coverage_list:
        new_dict[f] = []
        for time_range in file_coverage_list[f]:
            new_dict[f].append([spiceypy.et2datetime(time_range[0]), spiceypy.et2datetime(time_range[1])])
    return new_dict

def file_coverage_list_to_spacecraft_clock_time_strings(file_coverage_list):
    '''
    Returns a new file coverage dictionary that contains the spacecraft clock instead of seconds since J2000 epoch
    '''
    new_dict = {}
    for f in file_coverage_list:
        new_dict[f] = []
        for time_range in file_coverage_list[f]:
            try:
                new_dict[f].append([spiceypy.sce2s(SPACECRAFT_ID, time_range[0]), spiceypy.sce2s(SPACECRAFT_ID, time_range[1])])
            except Exception as e:
                new_dict[f].append([0, 0])
    return new_dict

def index_spice_files(spice_files):

    latest_sclk = get_latest_sclk()
    latest_lsk = get_latest_lsk()

    file_coverage_J2000 = get_coverage_dictionary(spice_files, latest_sclk, latest_lsk)
    file_coverage_datetime = file_coverage_list_to_datetime_strings(file_coverage_J2000)
    file_coverage_sclk = file_coverage_list_to_spacecraft_clock_time_strings(file_coverage_J2000)

    for f in file_coverage_J2000:
        delivery_date = datetime.fromtimestamp(os.path.getmtime(f))
        spice_params = {'ingestion_date': delivey_date}
        spice_metadata = extract_parts([attitude_file_regex, spacecraft_ephemeris_regex, spice_frame_regex, spice_prod_ver_regex],
                                        f,
                                        ['type', 'version'])
        spice_params['kernel_type'] = spice_metadata['type']
        spice_params['version'] = spice_metadata['version']
        spice_params['file_path'] = f
        spice_params['min_date_j2000'] = file_coverage_J2000[f][0][0]
        spice_params['max_date_j2000'] = file_coverage_J2000[f][-1][-1]
        spice_params['file_intervals_j2000'] = file_coverage_J2000[f]
        spice_params['min_date_datetime'] = file_coverage_datetime[f][0][0]
        spice_params['max_date_datetime'] = file_coverage_datetime[f][-1][-1]
        spice_params['file_intervals_datetime'] = file_coverage_datetime[f]
        spice_params['min_date_sclk'] = file_coverage_sclk[f][0][0]
        spice_params['max_date_sclk'] = file_coverage_sclk[f][-1][-1]
        spice_params['file_intervals_sclk'] = file_coverage_sclk[f]
        spice_params['lsk_kernel'] = latest_lsk
        spice_params['sclk_kernel'] = latest_sclk
        with db.Session() as session, session.begin():
            session.add(models.SPICEFiles(**spice_params))
        logger.info("Wrote data to the ScienceFiles table")

def create_symlink(source_path: Path, destination_path: Path) -> None:
    """Create a symlink from source_path to destination_path.

    Parameters
    ----------
    source_path : str
        Source path of the symlink
    destination_path : str
        Destination path of the symlink

    """
    # Remove the old symlink
    destination_path.unlink(missing_ok=True)

    # Create a new symlink pointing to the new file
    destination_path.symlink_to(source_path)


def write_data_to_efs(s3_key: str, s3_bucket: str):
    """Write data to EFS and create/update symlink.

    Parameters
    ----------
    s3_key : str
        S3 object key
    s3_bucket : str
        The S3 bucket

    """
    # Create an S3 client
    s3_client = boto3.client("s3")

    # Remove 'spice/' prefix from the s3 key. See key example below.
    #   Eg. spice/spin/imap_2025_122_2025_122_02.spin.csv
    # Keep remaining folder path after `spice/` to match the folder structure
    # defined in imap-data-access library.
    s3_folder_path = os.path.dirname(s3_key).replace("spice/", "")
    filename = os.path.basename(s3_key)
    # Download path to EFS
    efs_spice_path = spice_mount_path / s3_folder_path

    try:
        # Create the folder if it does not exist
        efs_spice_path.mkdir(parents=True, exist_ok=True)
        # Download file from S3 to the EFS path
        s3_client.download_file(s3_bucket, s3_key, efs_spice_path / filename)
        logger.info(f"{s3_key} file downloaded successfully")
    except Exception as e:
        logger.error(f"Error downloading file: {e!s}")

    logger.info("File was written to EFS path: %s", efs_spice_path)
    return efs_spice_path


def lambda_handler(event, context):
    """Lambda is triggered by eventbridge.

    Input looks like this:
    {
        "version": "0",
        "id": "3ee8fb2e-856d-790d-1d81-f77e1f3c0987",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "account": "449431850278",
        "time": "2023-10-25T23:53:17Z",
        "region": "us-west-2",
        "resources": [
            "arn:aws:s3:::sds-data-449431850278"
        ],
        "detail": {
            "version": "0",
            "bucket": {
                "name": "sds-data-449431850278"
            },
            "object": {
                "key": "spice/spin/imap_2025_122_2025_122_02.spin.csv",
                "size": 8,
                "etag": "fd33e2e8ad3cb1bdd3ea8f5633fcf5c7",
                "version-id": "w9eElv_lFFeEbifMabOBHjtJl9Ori_At",
                "sequencer": "006539AA6D7936ACF5"
            },
            "request-id": "5V837ESMXGRD39D2",
            "requester": "449431850278",
            "source-ip-address": "128.138.64.30",
            "reason": "PutObject"
        }
    }

    Parameters
    ----------
    event : dict
        Event input
    context : LambdaContext
        This object provides methods and properties that provide information
        about the invocation, function, and runtime environment.

    Returns
    -------
    dict
        Response message

    """
    # Retrieve the S3 bucket and key from the event
    s3_bucket = event["detail"]["bucket"]["name"]
    s3_key = event["detail"]["object"]["key"]
    logger.info(event)

    efs_spice_path = write_data_to_efs(s3_key, s3_bucket)

    if "mk" in efs_spice_path:
        filename_list=[efs_spice_path]
        index_spice_files(filename_list)
    else:
        #Redo everything
        filename_list = []
        dir_to_walk = str(spice_mount_path)
            for root, _, filenames in os.walk(dir_to_walk):
                for filename in filenames:
                    filename_list.append(os.path.join(root, filename))
        logger.info(f'All SPICE files found to reprocess: {filename_list}')
        index_spice_files(filename_list)
        create_new_metakernel()

    return {"statusCode": 200, "body": "File downloaded and moved successfully"}
