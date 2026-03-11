"""Functions to write SPICE ingested files to EFS."""

import csv
import json
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
from ..pipeline_lambdas.indexer import get_file_ingestion_date
from ..spice_utilities import download_from_s3, furnish_best_spice_file
from .lambda_custom_events import IMAPLambdaPutEvent

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Define constants needed in the file
EARTH_SPICE_ID = 3
SPACECRAFT_ID = -43
minimum_mission_time = datetime(2010, 1, 1)
maximum_mission_time = datetime(2145, 1, 1)
MAXIMUM_DATETIME_INTERVAL = [[minimum_mission_time, maximum_mission_time]]
MAXIMUM_SCLK_INTERVAL = [
    ["1/0000000000:00000", "1/4260211203:00000"]
]  # Calculated from the above datetimes seperately
MAXIMUM_J2000_INTERVAL = [
    [315576066.1839245, 4575787269.183866]
]  # Calculated from the above datetimes seperately

# Set constants for the time interval calculations
COVERAGE_ANGULAR_VELOCITY_ONLY = False  # Only include segments with angular velocity?
COVERAGE_SPICE_ARRAY_LENGTH = 10000  # Use an array size of 10000 for coverage calc
COVERAGE_LEVEL = "INTERVAL"  # the granularity at which the coverage is examined
COVERAGE_TOLERANCE = 0.0  # Tolerance value expressed in ticks of the spacecraft.
COVERAGE_TIME_SYSTEM = "TDB"  # Whether to use J2000 (TDB) or spacecraft clock (SCLK)


def clear_ephemeral_storage(downloaded_path: Path):
    """Delete downloaded temporary file from ephemeral storage.

    Parameters
    ----------
    downloaded_path : Path
        Path where file from s3 was downloaded.
    """
    try:
        if downloaded_path.exists():
            downloaded_path.unlink()
            logger.info(f"Deleted temporary file: {downloaded_path}")
    except Exception as e:
        logger.warning(f"Failed to delete temporary file {downloaded_path}: {e}")


def get_coverage_dictionary(spice_file: Path):
    """Determine the valid time spans of a SPICE file.

    Returns 3 lists for GPS time, python datetime, and spacecraft clock time.
    The lists are of the form:

    [[interval1_start, interval1_end], [interval2_start, interval2_end],
     [interval3_start, interval3_end] ... ]

    Parameters
    ----------
    spice_file: Path
        The path to the spice file

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

    # cover defines the array we need to write to
    cover = spiceypy.cell_double(COVERAGE_SPICE_ARRAY_LENGTH)

    # 1) Calculate the time coverage of the file
    if spice_file.suffix == ".bc":
        # get the objects covered by the CK
        objs = spiceypy.ckobj(str(spice_file))
        if len(objs) > 1:
            raise ValueError(
                f"Unable to handle ck files with more than one object. "
                f"Found {len(objs)} objects in {spice_file}"
            )
        cover = spiceypy.ckcov(
            str(spice_file),
            idcode=objs[0],
            cover=cover,
            needav=COVERAGE_ANGULAR_VELOCITY_ONLY,
            level=COVERAGE_LEVEL,
            tol=COVERAGE_TOLERANCE,
            timsys=COVERAGE_TIME_SYSTEM,
        )
    elif spice_file.suffix == ".bsp":
        cover = spiceypy.spkcov(str(spice_file), idcode=SPACECRAFT_ID, cover=cover)
    elif spice_file.suffix == ".bpc":
        # pckcov does *not* return a new "cover" object.
        # Instead, we retrieve it from the original input, which is a mutable object.
        spiceypy.pckcov(str(spice_file), idcode=EARTH_SPICE_ID * 1000, cover=cover)
    else:
        raise ValueError(
            f"Unable to handle spice file with the extension {spice_file.suffix}."
        )

    # 2) Determine the number of intervals in the file
    card = spiceypy.wncard(cover)
    # 3) Loop through the number of intervals, appending the results of steps 4,5,6
    for i_window in range(card):
        # 4) Retrieve the time span of each interval
        (left, right) = spiceypy.wnfetd(cover, i_window)
        # Make sure that the left interval is not before the minimum mission time
        left = max(left, MAXIMUM_J2000_INTERVAL[0][0])
        # 5) Throw out any singleton points. You cannot interpolate between these.
        if left != right:
            results_j2000.append([left, right])
            # 6) Convert the time span to datetime
            results_datetime.append(
                [spiceypy.et2datetime(left), spiceypy.et2datetime(right)]
            )
            # 7) Convert the time span to spacecraft clock time
            results_sclk.append(
                [
                    spiceypy.sce2s(SPACECRAFT_ID, left),
                    spiceypy.sce2s(SPACECRAFT_ID, right),
                ]
            )

    return results_j2000, results_datetime, results_sclk


def _upsert_into_spice_table(
    s3_key: str,
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
    s3_key: str
        The S3 path of the SPICE file to upsert
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
    filename = str(spice_object.filename.name)
    # earth attitude kernel doesn't have version.
    if spice_object.spice_metadata["type"] == "earth_attitude":
        version = "1"
    else:
        version = spice_object.spice_metadata["version"]
    spice_params = {
        "file_path": s3_key,
        "file_name": filename,
        "ingestion_date": get_file_ingestion_date(s3_key),
        "file_root": "".join(filename.rsplit(version, 1)),
        "kernel_type": spice_object.spice_metadata["type"],
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
        "sclk_kernel": str(latest_sclk),
        "lsk_kernel": str(latest_lsk),
        "version": version,
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


def index_spice_file(s3_key: str):
    """Insert SPICE file metadata into SPICE database table.

    Parameters
    ----------
    s3_key: str
        Path of kernel file in S3 bucket.
    """
    latest_lsk = None
    latest_sclk = None
    filename = os.path.basename(s3_key)
    spice_object = SPICEFilePath(filename)
    spice_metadata = spice_object.spice_metadata
    # Download the ingested SPICE file from S3
    try:
        spice_file = download_from_s3(s3_key)
    except Exception as e:
        logger.error(f"Failed to download SPICE file {s3_key}: {e}")
        raise ValueError(f"Error downloading file {s3_key}") from e

    # Load time coverage data from the SPICE file
    try:
        latest_lsk = furnish_best_spice_file("leapseconds")
        latest_sclk = furnish_best_spice_file("spacecraft_clock")
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
            file_coverage_j2000, file_coverage_datetime, file_coverage_sclk = (
                get_coverage_dictionary(spice_file)
            )

    # Insert/Update the gathered data into the database
    _upsert_into_spice_table(
        s3_key,
        spice_object,
        file_coverage_j2000,
        file_coverage_datetime,
        file_coverage_sclk,
        latest_lsk,
        latest_sclk,
    )
    # NOTE: Only clear the current SPICE file from ephemeral storage.
    # Time kernels (leapseconds and spacecraft clock) are kept in ephemeral
    # storage for potential reuse by other operations. Since all downloaded
    # files are stored in '/tmp' (ephemeral storage), they will be
    # automatically cleaned up when the Lambda execution ends.
    clear_ephemeral_storage(spice_file)


def index_spin_file(s3_key: Path):
    """Insert spin file metadata into spin database table.

    Parameters
    ----------
    s3_key: str
        S3 path of the spin file.
    """
    with db.Session() as session:
        spin_obj = SPICEFilePath(os.path.basename(s3_key))
        spin_metadata = spin_obj.spice_metadata
        params = {
            "file_path": s3_key,
            "start_date": spin_metadata["start_date"],
            "end_date": spin_metadata["end_date"],
            "version": spin_metadata["version"],
            "ingestion_date": get_file_ingestion_date(s3_key),
        }
        spin_table = models.SpinFiles(**params)
        session.add(spin_table)
        session.commit()


def index_repoint_file(s3_key):
    """Insert repoint file metadata into repoint database table.

    Parameters
    ----------
    s3_key: str
        S3 path of the repoint file.
    """
    with db.Session() as session:
        repoint_obj = SPICEFilePath(os.path.basename(s3_key))
        metadata = repoint_obj.spice_metadata

        # Query Pointing Table to get the exact date/time of the latest data
        # in the repoint file. This requires that `index_pointing_data` is run
        # before indexing the repoint file.
        final_pointings = (
            session.query(models.PointingTable)
            .order_by(models.PointingTable.pointing_id.desc())
            .limit(2)
            .all()
        )
        # The repoint end time should be the last Pointing start time if it is not
        # null. Otherwise, it should be the second-to-last repoint start time.
        end_date = (
            final_pointings[0].pointing_start_utc
            or final_pointings[1].repoint_start_utc
        )

        params = {
            "file_path": s3_key,
            "end_date": end_date,
            "version": metadata["version"],
            "ingestion_date": get_file_ingestion_date(s3_key),
        }
        repoint_table = models.RepointFiles(**params)
        session.add(repoint_table)
        session.commit()

    logger.info(f"Indexed {s3_key} to SPICEFiles table")


def index_small_forces_file(s3_key):
    """Insert small-forces file metadata into small-forces database table.

    Parameters
    ----------
    s3_key: str
        S3 path of the small forces file.
    """
    with db.Session() as session:
        small_forces_obj = SPICEFilePath(os.path.basename(s3_key))
        metadata = small_forces_obj.spice_metadata

        params = {
            "file_path": s3_key,
            "start_date": metadata["start_date"],
            "end_date": metadata["end_date"],
            "version": metadata["version"],
            "ingestion_date": get_file_ingestion_date(s3_key),
        }
        small_forces_table = models.SmallForcesFile(**params)
        session.add(small_forces_table)
        session.commit()

    logger.info(f"Indexed {s3_key} to SmallForcesFile table")


def parse_datetime(val):
    """Parse a datetime string safely, returning None for invalid inputs.

    Parameters
    ----------
    val: str
        The datetime string to parse.

    Returns
    -------
    datetime or None
        The parsed datetime object or None if parsing failed.
    """
    if val is None or str(val).strip().lower() in ("", "nan", "none"):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def index_pointing_data(s3_key: str):
    """Insert pointing data into pointing database table.

    Pointing data is derived from the repoint file data. Steps:
    * Download the repoint file from S3
    * Read the CSV file
    * Filter repoint_id that's not in pointing_table
    * Fill rows with None values with new values
    * For each new repoint_id, calculate pointing_start_utc and pointing_end_utc
        Formula are:
        pointing_start_utc = repoint_end_utc of repoint_id
        pointing_end_utc = repoint_end_utc of repoint_id + 1
        repoint_start_utc = repoint_start_utc of repoint_id + 1
        repoint_end_utc = repoint_end_utc of repoint_id + 1
    * Insert into pointing table

    Parameters
    ----------
    s3_key: str
        S3 path of the repoint file.
    """
    logger.info(f"Indexing {s3_key} to pointing table")
    # Download repoint file
    repoint_file_path = download_from_s3(s3_key)
    repoind_data = []
    repoint_db_records = []
    # Read CSV file using Python's native csv module
    with open(repoint_file_path) as file:
        reader = csv.DictReader(file)
        repoind_data = list(reader)

    # Filter out rows with empty repoint_id or all values are empty
    # This can happen if there is empty row in the CSV file
    repoind_data = [
        row for row in repoind_data if any(row.values()) and row["repoint_id"].strip()
    ]
    for i_row, data in enumerate(repoind_data[:-1]):
        # Since for loop stops at -1, we can assume that next row exists
        # and should be able to calculate the pointing data
        current_row = repoind_data[i_row]
        next_row = repoind_data[i_row + 1]
        row_data = {
            # Converting to int to match the SQL type
            "pointing_id": int(data["repoint_id"]),
            "pointing_start_utc": parse_datetime(current_row["repoint_end_utc"]),
            "pointing_end_utc": parse_datetime(next_row["repoint_end_utc"]),
            "repoint_start_utc": parse_datetime(next_row["repoint_start_utc"]),
            "repoint_end_utc": parse_datetime(next_row["repoint_end_utc"]),
        }
        repoint_db_records.append(row_data)

    # Store last record data
    last_row = repoind_data[-1]
    row_data = {
        "pointing_id": int(last_row["repoint_id"]),
        "pointing_start_utc": parse_datetime(last_row["repoint_end_utc"]),
        "pointing_end_utc": None,
        "repoint_start_utc": None,
        "repoint_end_utc": None,
    }
    repoint_db_records.append(row_data)

    with db.Session() as session:
        # Similar to _upsert_into_spice_table, update db to latest repoint
        # if data already exists. Otherwise, insert new data. This will
        # take care of the None values or new updated values.
        records = insert(models.PointingTable).values(repoint_db_records)
        records = records.on_conflict_do_update(
            index_elements=["pointing_id"],
            set_={
                "pointing_start_utc": records.excluded.pointing_start_utc,
                "pointing_end_utc": records.excluded.pointing_end_utc,
                "repoint_start_utc": records.excluded.repoint_start_utc,
                "repoint_end_utc": records.excluded.repoint_end_utc,
            },
        )
        session.execute(records)
        session.commit()


def send_spice_event(spice_obj: SPICEFilePath, s3_key: str):
    """Send SPICE event to EventBridge.

    Example of what PutEvent looks like:
    {
        "Source": "imap.lambda",
        "DetailType": "Processed File",
        "Detail": {
            "object": {
                "key": "imap/spice/spin/imap_2025_122_2025_122_02.spin.csv",
                "instrument": "spacecraft",
                }
        }
    }

    Parameters
    ----------
    spice_obj : SPICEFilePath
        SPICE of the file to determine the event type
    s3_key : str
        S3 object key to send to EventBridge
    """
    # If these kernels, send event to EventBridge
    spice_events = [
        "attitude_history",
        "attitude_predict",
        "ephemeris_reconstructed",
        "ephemeris_nominal",
        "ephemeris_predict",
        "spin",
        "repoint",
        "thruster",
    ]
    if spice_obj.spice_metadata["type"] not in spice_events:
        return None

    # Create event["detail"] and event inputs
    detail = {
        "object": {
            "key": s3_key,
        }
    }

    eventbridge_client = boto3.client("events")
    # In order to trigger batch starter, the event must have a key, instrument and
    # data_level. Otherwise, it sends event but never makes it because of SQS filter
    # policy
    detail["object"]["instrument"] = "spacecraft"
    detail["object"]["data_level"] = "l1a"

    event = IMAPLambdaPutEvent(
        detail_type="Processed File",
        detail=detail,
    )
    event_data = event.to_event()

    logger.info(
        f"Sending SPICE event for {s3_key} to EventBridge"
        f" with detail {json.dumps(detail)}"
    )

    # Send event to EventBridge
    response = eventbridge_client.put_events(Entries=[event_data])
    logger.info(f"Event sent to EventBridge: {response}")
    return response


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
                "key": "imap/spice/spin/imap_2025_122_2025_122_02.spin.csv",
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
    logger.info("SPICE Indexer event: " + json.dumps(event, indent=2))

    # Retrieve the S3 bucket and key from the event
    s3_key = event["detail"]["object"]["key"]

    spice_obj = SPICEFilePath(os.path.basename(s3_key))

    # Index file to its respective table
    if spice_obj.spice_metadata["type"] == "repoint":
        index_pointing_data(s3_key)
        index_repoint_file(s3_key)
    elif spice_obj.spice_metadata["type"] == "spin":
        logger.info(f"Indexing {s3_key} spin table")
        index_spin_file(s3_key)
    elif spice_obj.spice_metadata["type"] == "thruster":
        logger.info(f"Indexing {s3_key} small-forces table")
        index_small_forces_file(s3_key)
    else:
        # Index the SPICE kernels to the SPICE table
        logger.info(f"Indexing {s3_key} to SPICE table")
        index_spice_file(s3_key)

    send_spice_event(spice_obj, s3_key)

    return {
        "statusCode": 200,
        "body": f"{s3_key} moved to EFS and indexed to table successfully",
    }
