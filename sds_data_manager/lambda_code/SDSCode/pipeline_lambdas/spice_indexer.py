"""Functions to write SPICE ingested files to EFS."""

import logging
import os
from datetime import datetime
from pathlib import Path

import boto3
import spiceypy
from imap_data_access import SPICEFilePath
from sqlalchemy.dialects.postgresql import insert

from ..database import database as db
from ..database import models

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# Define the paths
SPACECRAFT_ID = -43
minimum_mission_time = datetime(2023, 1, 1)
maximum_mission_time = datetime(2145, 1, 1)
MAXIMUM_DATETIME_INTERVAL = [[minimum_mission_time, maximum_mission_time]]
MAXIMUM_SCLK_INTERVAL = [
    ["1/0410227203:00000", "1/4288750963:38093"]
]  # Calculated from the above datetimes seperately
MAXIMUM_J2000_INTERVAL = [
    [725803269.1839136, 4575787269.183866]
]  # Calculated from the above datetimes seperately


def furnish_best_spice_file(spice_path: Path):
    """Furnish the best kernel from spice_path.

    Parameters
    ----------
    spice_path: Path
        The path to the direcory where SPICE is stored

    Returns
    -------
    highest_version_spice_file: Path
        The path to the SPICE file that was furnished
    """
    kernels_sorted = sorted([f for f in spice_path.iterdir() if f.is_file()])
    if kernels_sorted:
        highest_version_spice_file = spice_path / kernels_sorted[-1]
        spiceypy.furnsh(str(highest_version_spice_file))
        return highest_version_spice_file
    else:
        raise FileNotFoundError(f"No SPICE files found in the directory {spice_path}")


def get_coverage_dictionary(spice_file: Path, **kwargs):
    """Determine the valid time spans of a SPICE file.

    Returns 3 lists for GPS time, python datetime, and spacecraft clock time.
    The lists are of the form:

    [[interval1_start, interval1_end], [interval2_start, interval2_end],
     [interval3_start, interval3_end] ... ]

    Parameters
    ----------
    spice_file: Path
        The path to the spice file
    kwargs: dict
        The key word arguments to use when determining the coverage dictionary

    Returns
    -------
    results_j2000: list[list[float]]
        The results in SPICE J2000 time
    results_datetime: list[list[datetime]]
        The results as python datetime objects
    results_sclk: list[list[str]]
        The results using spacecraft clock time notation
    """
    results_j2000 = []
    results_sclk = []
    results_datetime = []

    if spice_file.suffix == ".bc":
        coverage_function = spiceypy.ckcov
    elif spice_file.suffix == ".bsp":
        coverage_function = spiceypy.spkcov
    else:
        raise ValueError(
            f"Unable to handle spice file with the extension {spice_file.suffix}."
        )

    cover = coverage_function(str(spice_file), **kwargs)
    card = spiceypy.wncard(cover)
    for i_window in range(card):
        (left, right) = spiceypy.wnfetd(cover, i_window)
        results_j2000.append([left, right])
        results_datetime.append(
            [spiceypy.et2datetime(left), spiceypy.et2datetime(right)]
        )
        results_sclk.append(
            [
                spiceypy.sce2s(SPACECRAFT_ID, left),
                spiceypy.sce2s(SPACECRAFT_ID, right),
            ]
        )

    return results_j2000, results_datetime, results_sclk


def _upsert_into_spice_table(
    spice_object: SPICEFilePath,
    file_coverage_j2000: list[list[float]],
    file_coverage_datetime: list[list[datetime]],
    file_coverage_sclk: list[list[str]],
    latest_sclk: Path,
    latest_lsk: Path,
):
    """Insert/Update the spice metadata table with collected data.

    Parameters
    ----------
    spice_object: SPICEFilePath
        The SPICE file to upsert
    file_coverage_j2000: list[list[float]]
        A list of file intervals in j2000 time format
    file_coverage_datetime: list[list[datetime]]
        A list of file intervals in datetime format
    file_coverage_sclk: list[list[str]]
        A list of file intervals in sclk string format
    latest_sclk: Path
        The latest clock kernel used for the above calculations
    latest_lsk: Path
        The latest leapsecond kernel used for the above calculations
    """
    # Format the data to insert
    spice_params = {
        "ingestion_date": datetime.now(),
        "kernel_type": spice_object.spice_metadata["type"],
        "version": spice_object.spice_metadata["version"],
        "file_name": str(spice_object.filename.name),
        "file_root": str(spice_object.filename.name).replace(
            str(spice_object.spice_metadata["version"]), ""
        ),
        "min_date_j2000": file_coverage_j2000[0][0],
        "max_date_j2000": file_coverage_j2000[-1][-1],
        "file_intervals_j2000": file_coverage_j2000,
        "min_date_datetime": file_coverage_datetime[0][0],
        "max_date_datetime": file_coverage_datetime[-1][-1],
        "file_intervals_datetime": [
            [dt.isoformat() for dt in sublist] for sublist in file_coverage_datetime
        ],
        "min_date_sclk": file_coverage_sclk[0][0],
        "max_date_sclk": file_coverage_sclk[-1][-1],
        "file_intervals_sclk": file_coverage_sclk,
        "lsk_kernel": str(latest_lsk),
        "sclk_kernel": str(latest_sclk),
    }

    with db.Session() as session:
        # Execute the statement as a single "insert-or-update" operation
        stmt = (
            insert(models.SPICEFiles)
            .values(**spice_params)
            .on_conflict_do_update(
                index_elements=["file_name"],  # or name of a unique constraint
                set_={  # Remove the "file_name" from the update dict
                    key: spice_params[key]
                    for key in spice_params.keys()
                    if key != "file_name"
                },
            )
        )
        session.execute(stmt)
        session.commit()
    logger.info(f"Wrote {spice_params} to the SPICEFiles table")


def index_spice_file(spice_file: Path):
    """Insert SPICE file metadata into SPICE database table.

    Parameters
    ----------
    spice_file: Path
        The full name and path the SPICE file to index
    """
    latest_lsk = None
    latest_sclk = None
    spice_object = SPICEFilePath(spice_file)
    spice_metadata = SPICEFilePath(spice_file).spice_metadata
    try:
        latest_lsk = furnish_best_spice_file(spice_file.parent.parent / "lsk")
        latest_sclk = furnish_best_spice_file(spice_file.parent.parent / "sclk")
    except FileNotFoundError as e:
        if spice_metadata["type"] in ("leapseconds", "spacecraft_clock"):
            # This block will likely only be reached if this is the very first
            # leapsecond or spacecraft_clock kernel placed on the SDS. In this case,
            # we'll insert default data and continue.
            file_coverage_datetime = MAXIMUM_DATETIME_INTERVAL
            file_coverage_j2000 = MAXIMUM_J2000_INTERVAL
            file_coverage_sclk = MAXIMUM_SCLK_INTERVAL
        else:
            raise e

    if latest_lsk and latest_sclk:  # clock and leapsecond kernels are loaded
        if spice_metadata["start_date"] is None or spice_metadata["end_date"] is None:
            # In this block, we have files that do NOT need to have
            # any file_intervals calculated. We will use the maximum time range.
            if spice_metadata["start_date"] is None:
                spice_metadata["start_date"] = minimum_mission_time
            if spice_metadata["end_date"] is None:
                spice_metadata["end_date"] = maximum_mission_time
            file_coverage_datetime = [
                [spice_metadata["start_date"], spice_metadata["end_date"]]
            ]
            file_coverage_j2000 = [
                [
                    spiceypy.datetime2et(spice_metadata["start_date"]),
                    spiceypy.datetime2et(spice_metadata["end_date"]),
                ]
            ]
            file_coverage_sclk = [
                [
                    spiceypy.sce2s(SPACECRAFT_ID, file_coverage_j2000[0][0]),
                    spiceypy.sce2s(SPACECRAFT_ID, file_coverage_j2000[0][1]),
                ]
            ]
        else:
            function_arguments = {
                "idcode": SPACECRAFT_ID * 1000,
                "cover": spiceypy.cell_double(10000),
            }
            if "attitude" in spice_metadata["type"]:
                function_arguments["needav"] = False
                function_arguments["level"] = "INTERVAL"
                function_arguments["tol"] = 1000000.0
                function_arguments["timsys"] = "TDB"
            file_coverage_j2000, file_coverage_datetime, file_coverage_sclk = (
                get_coverage_dictionary(spice_file, **function_arguments)
            )

    # Insert/Update the gathered data into the database
    _upsert_into_spice_table(
        spice_object,
        file_coverage_j2000,
        file_coverage_datetime,
        file_coverage_sclk,
        latest_lsk,
        latest_sclk,
    )


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


def write_data_to_efs(s3_key: str, s3_bucket: str, spice_mount_path: Path) -> Path:
    """Write data to EFS and create/update symlink.

    Parameters
    ----------
    s3_key : str
        S3 object key
    s3_bucket : str
        The S3 bucket
    spice_mount_path: Path
        The path to the local SPICE directory

    Returns
    -------
    efs_spice_filename_and_path : Path
        The local location of the SPICE file

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
    # Define the paths
    spice_mount_path = Path(os.getenv("EFS_SPICE_MOUNT_PATH"))  # Eg. /mnt/spice

    # Retrieve the S3 bucket and key from the event
    s3_bucket = event["detail"]["bucket"]["name"]
    s3_key = event["detail"]["object"]["key"]
    logger.info(event)

    file_path = write_data_to_efs(s3_key, s3_bucket, spice_mount_path)
    index_spice_file(file_path)

    return {"statusCode": 200, "body": "File downloaded and moved successfully"}
