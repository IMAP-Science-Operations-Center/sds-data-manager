"""Functions to write SPICE ingested files to EFS."""

import logging
import os
from pathlib import Path

import boto3
import datetime 
import spiceypy
from imap_data_access import SPICEFilePath
from SDSCode.database import database as db
from SDSCode.database import models

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# Define the paths
spice_mount_path = Path(os.getenv("EFS_SPICE_MOUNT_PATH"))  # Eg. /mnt/spice
minimum_mission_time = datetime(2023, 1, 1)
maximum_mission_time = datetime(2999, 1, 1)
SPACECRAFT_ID = -43000
DEFAULT_DATETIME_INTERVAL = [[minimum_mission_time,maximum_mission_time]]
DEFAULT_J2000_INTERVAL = [[spiceypy.datetime2et(minimum_mission_time), spiceypy.datetime2et(maximum_mission_time)]]

# Define the paths
spice_mount_path = Path(os.getenv("EFS_SPICE_MOUNT_PATH"))  # Eg. /mnt/spice

def get_latest_from_dir(spice_type):
    sclk_sorted = sorted(os.listdir(f"{str(spice_mount_path)}/{spice_type}"))
    if sclk_sorted :
        return str(spice_mount_path) + f"/{spice_type}/" + sclk_sorted[-1]

def furnish_lsk_sclk():
    latest_sclk = get_latest_from_dir('sclk')
    latest_lsk = get_latest_from_dir('lsk')
    try:
        spiceypy.furnsh(latest_sclk)
        spiceypy.furnsh(latest_lsk)
    except:
        raise Exception("Unable to load spacecraft clock and/or leapseconds kernel.  "
                       "Returning empty values for file coverage.")
    return latest_lsk, latest_sclk

def get_coverage_dictionary(spice_file, coverage_function, function_arguments):
    '''
    Returns a list of lists.  For example:
    [[interval1_start, interval1_end], [interval2_start, interval2_end],
     [interval3_start, interval3_end]]
    '''
    results_J2000 = []
    results_sclk = []
    results_datetime = []
    
    try:
        cover = coverage_function(spice_file, **function_arguments)
        card = spiceypy.wncard(cover)
        for i_window in range(card):
            (left, right) = spiceypy.wnfetd(cover, i_window)
            results_J2000.append([left, right])
            results_datetime.append([spiceypy.et2datetime(left), spiceypy.et2datetime(right)])
            results_sclk.append([spiceypy.sce2s(SPACECRAFT_ID, left), spiceypy.sce2s(SPACECRAFT_ID, right)])
    except Exception as e:
        raise Exception(f"Unable to gather coverage information from file {spice_file} due to error {str(e)}.  Not indexing!")  
    return results_J2000, results_datetime, results_sclk

def index_spice_file(spice_file, latest_lsk, latest_sclk):
    DEFAULT_SCLK_INTERVAL = [[spiceypy.sce2s(SPACECRAFT_ID, minimum_mission_time), spiceypy.sce2s(SPACECRAFT_ID, maximum_mission_time)]]
    spice_metadata = SPICEFilePath.extract_filename_components(spice_file)
    if os.path.splitext(spice_file)[1] in (".bc", ".spk"):
        coverage_function = spiceypy.spkcov
        function_arguments = {'idcode':SPACECRAFT_ID,
                                'cover': spiceypy.cell_double(10000)
                                }
        if 'attitude' in spice_metadata['type']:
            coverage_function = spiceypy.ckcov
            function_arguments['needav'] = False
            function_arguments['level'] = 'INTERVAL'
            function_arguments['tol'] =  0.0
            function_arguments['timsys'] = 'TDB'
            
        file_coverage_J2000, file_coverage_datetime, file_coverage_sclk = get_coverage_dictionary(spice_file, coverage_function, function_arguments)   
    else:
        file_coverage_J2000, file_coverage_datetime, file_coverage_sclk = DEFAULT_J2000_INTERVAL, DEFAULT_DATETIME_INTERVAL, DEFAULT_SCLK_INTERVAL

    spice_params = {}
    spice_params['ingestion_date'] = datetime.fromtimestamp(os.path.getmtime(spice_file))
    spice_params['kernel_type'] = spice_metadata['type']
    spice_params['version'] = spice_metadata['version']
    spice_params['file_path'] = spice_file
    spice_params['file_root'] = spice_file.replace(spice_metadata['version'], '')
    spice_params['min_date_j2000'] = file_coverage_J2000[0][0]
    spice_params['max_date_j2000'] = file_coverage_J2000[-1][-1]
    spice_params['file_intervals_j2000'] = file_coverage_J2000
    spice_params['min_date_datetime'] = file_coverage_datetime[0][0]
    spice_params['max_date_datetime'] = file_coverage_datetime[-1][-1]
    spice_params['file_intervals_datetime'] = [dt.isoformat() for dt in file_coverage_datetime]
    spice_params['min_date_sclk'] = file_coverage_sclk[0][0]
    spice_params['max_date_sclk'] = file_coverage_sclk[-1][-1]
    spice_params['file_intervals_sclk'] = file_coverage_sclk
    spice_params['lsk_kernel'] = latest_lsk
    spice_params['sclk_kernel'] = latest_sclk

    with db.Session() as session, session.begin():
        # Check if the record already exists
        existing_entry = session.query(models.SPICEFiles).filter_by(file_path=spice_params['file_path']).first()
        if existing_entry:
            for key, value in spice_params.items():
                if key == 'file_path':
                    continue
                setattr(existing_entry, key, value)  # Update existing record
        else:
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
    efs_spice_filename_and_path = efs_spice_path / filename
    try:
        # Create the folder if it does not exist
        efs_spice_path.mkdir(parents=True, exist_ok=True)
        # Download file from S3 to the EFS path
        s3_client.download_file(s3_bucket, s3_key, efs_spice_filename_and_path)
        logger.info(f"{s3_key} file downloaded successfully")
    except Exception as e:
        logger.error(f"Error downloading file: {e!s}")

    logger.info("File was written to EFS path: %s", efs_spice_path)
    return efs_spice_filename_and_path


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

    file_path = write_data_to_efs(s3_key, s3_bucket)
    latest_lsk, latest_sclk = furnish_lsk_sclk()
    index_spice_file(file_path, latest_lsk, latest_sclk)
    
    return {"statusCode": 200, "body": "File downloaded and moved successfully"}
