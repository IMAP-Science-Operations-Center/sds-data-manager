"""IALiRT ingest lambda."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import boto3
import botocore
import imap_data_access
import numpy as np
import pandas as pd
import requests
import spiceypy
import xarray as xr
from boto3.dynamodb.conditions import Key
from imap_data_access.processing_input import (
    ProcessingInputCollection,
    SPICEInput,
    SPICESource,
)
from imap_processing import imap_module_directory
from imap_processing.cdf.utils import load_cdf
from imap_processing.ialirt.l0.parse_mag import process_packet
from imap_processing.ialirt.l0.process_codice import process_codice
from imap_processing.ialirt.l0.process_hit import process_hit
from imap_processing.ialirt.l0.process_swapi import process_swapi_ialirt
from imap_processing.ialirt.l0.process_swe import process_swe
from imap_processing.spice.geometry import (
    SpiceBody,
    SpiceFrame,
    imap_state,
)
from imap_processing.spice.time import (
    et_to_met,
    et_to_ttj2000ns,
    met_to_sclkticks,
    met_to_ttj2000ns,
    met_to_utc,
    sct_to_et,
    str_to_et,
)
from imap_processing.utils import packet_file_to_datasets

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

KERNELS = {
    "ephemeris_predicted",
    "ephemeris_90days",
    "planetary_ephemeris",
    "spacecraft_clock",
    "leapseconds",
    "imap_frames",
    "science_frames",
    "planetary_constants",
}
EFS_BASE_PATH = Path("/mnt/data")


def get_ancillary(instrument, descriptor):
    """Query and download ancillary data if not already present.

    Parameters
    ----------
    instrument : str
        The name of the instrument.
    descriptor : str
        The name of the descriptor.

    Returns
    -------
    download_path : Path
        Download path of calibration file.
    """
    imap_data_access.config["DATA_DIR"] = EFS_BASE_PATH
    calibration_files = imap_data_access.query(
        table="ancillary",
        instrument=instrument,
        descriptor=descriptor,
        version="latest",
    )

    if not calibration_files:
        raise FileNotFoundError(
            f"No calibration file found for {instrument=}, {descriptor=}"
        )

    calibration_file = sorted(
        calibration_files, key=lambda x: (x["start_date"], x["version"]), reverse=True
    )[0]

    download_path = imap_data_access.download(calibration_file["file_path"])
    logger.info(f"Adding to {download_path} to calibration files.")

    return download_path


def get_latest_spice_kernels(url: str) -> ProcessingInputCollection:
    """Query the SPICE metakernel API for latest SPICE kernel filenames.

    Parameters
    ----------
    url: str
        AWS account name.

    Returns
    -------
    dependency_inputs: ProcessingInputCollection
        A collection containing a SPICEInput object with the list of kernel filenames
        returned from the metakernel API.
    """
    dependency_inputs = ProcessingInputCollection()

    now = datetime.now(timezone.utc)
    one_week_ago = now - timedelta(weeks=1)
    # Define J2000 epoch: 2000-01-01T12:00:00 UTC
    # TODO: remove this once Bryan changes takes in 'yyyymmdd' format
    j2000 = datetime(2000, 1, 1, 11, 58, 56, tzinfo=timezone.utc)
    et_end_time = (now - j2000).total_seconds()
    et_start_time = (one_week_ago - j2000).total_seconds()

    file_types = ",".join(KERNELS)
    metakernel_url = url + "/metakernel"

    params = {
        "start_time": str(int(et_start_time)),
        "end_time": str(int(et_end_time)),
        "list_files": "True",
        "file_types": file_types,
    }

    logger.info(f"Sending request to {metakernel_url} with params: {params}")
    response = requests.get(metakernel_url, params=params, timeout=10)
    metakernel_files = response.json()

    logger.info(f"Found metakernel files: {metakernel_files}. Adding to collection.")
    dependency_inputs.add(SPICEInput(*metakernel_files))

    return dependency_inputs


def download_spice_file(dependencies) -> list[Path]:
    """Download SPICE kernel files from the IMAP data archive and store them in EFS.

    Parameters
    ----------
    dependencies: ProcessingInputCollection
        A collection containing a SPICEInput object with the list of kernel filenames
        returned from the metakernel API.

    Returns
    -------
    spice_files: list[Path]
        A list of Path objects representing the SPICE files stored in EFS.

    Notes
    -----
    List is priority ordered so furnishing in order results in correct SPICE priority.
    """
    imap_data_access.config["DATA_DIR"] = EFS_BASE_PATH
    dependencies.download_all_files()

    spice_files = dependencies.get_file_paths(data_type=SPICESource.SPICE.value)
    logger.info(f"Downloaded SPICE files: {spice_files}. Furnishing kernels.")
    spiceypy.furnsh([str(file.resolve()) for file in spice_files])

    return spice_files


def query_filenames(bucket: str, region: str, now: datetime):
    """Query the packets in the s3 bucket.

    Parameters
    ----------
    bucket : str
        The name of the S3 bucket.
    region : str
        The region in which the s3 bucket resides.
    now : datetime
        The current time in UTC.

    Returns
    -------
    filenames : list
        List of file paths.
    """
    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    five_minutes_ago = now - timedelta(minutes=5)

    # Account for any cases in which data spans a threshold since
    # s3 only uses prefixes for queries.
    # Example:
    # now = 2026-01-01T00:02:00Z
    # five_minutes_ago = 2025-12-31T23:57:00Z
    first_prefix = five_minutes_ago.strftime("packets/iois_1_packets_%Y_%j_%H_")
    second_prefix = now.strftime("packets/iois_1_packets_%Y_%j_%H_")

    first_response = s3_client.list_objects_v2(Bucket=bucket, Prefix=first_prefix)
    objects = first_response.get("Contents", [])

    if second_prefix != first_prefix:
        second_response = s3_client.list_objects_v2(Bucket=bucket, Prefix=second_prefix)
        objects.extend(second_response.get("Contents", []))

    filenames = []
    for obj in objects:
        key = obj["Key"]
        timestamp_str = key.split("iois_1_packets_")[1]
        timestamp_str = timestamp_str.removesuffix(".bin")
        timestamp = datetime.strptime(timestamp_str, "%Y_%j_%H_%M_%S")
        timestamp = timestamp.replace(tzinfo=timezone.utc)

        if five_minutes_ago <= timestamp <= now:
            filenames.append(key)

    return filenames


def parse_packets(filenames: list, bucket: str, download_dir: Path, apid=478):
    """Get packets into datasets and combine.

    This function is an event handler for s3 ingest bucket.
    It is also used to ingest data to the DynamoDB table.

    Parameters
    ----------
    filenames : list
        List of file paths.
    bucket : str
        The name of the S3 bucket.
    download_dir : Path
        The directory where the file will be downloaded.
    apid : int
        The apid of the packet to be processed.

    Returns
    -------
    combined : xr.Dataset
        Combined dataset.
    """
    s3 = boto3.client("s3")
    xtce_ialirt_path = (
        imap_module_directory / "ialirt" / "packet_definitions" / "ialirt.xml"
    )
    datasets = []

    for filename in filenames:
        local_path = download_dir / Path(filename).name
        s3.download_file(bucket, filename, str(local_path))
        xarray_data = packet_file_to_datasets(local_path, xtce_ialirt_path)[apid]
        datasets.append(xarray_data)

    combined = xr.concat(datasets, dim="epoch")
    # Drop duplicate epochs. This could happen if there are duplicate packets.
    _, unique_idx = np.unique(combined["epoch"], return_index=True)
    combined = combined.isel(epoch=sorted(unique_idx))

    return combined


def process_algorithms(  # noqa: PLR0912, PLR0915
    combined: xr.Dataset, algorithm_table, table_name, kernel_set_key
):
    """Process the algorithms and insert data, as needed.

    Parameters
    ----------
    combined : xr.Dataset
        L0 parsed data.
    algorithm_table : dynamodb.Table
        The DynamoDB table to insert or update the data.
    table_name : str
        Name of the DynamoDB table
    kernel_set_key : str, optional
        The kernel set identifier.
    """
    processors = [
        ("mag", process_packet),
        ("hit", process_hit),
        ("swe", process_swe),
        ("codice_lo", process_codice),
        ("codice_hi", process_codice),
        ("swapi", process_swapi_ialirt),
    ]

    # Collect any errors during processing to raise at the end
    processing_errors = []

    for instrument, process_func in processors:
        try:
            if instrument == "swe":
                logger.info("Processing SWE.")
                download_path = get_ancillary(instrument, "l1b-in-flight-cal")
                logger.info("swe l1b-in-flight-cal: %s", download_path)
                result = process_func(combined, [download_path])
            elif instrument == "mag":
                logger.info("Processing MAG.")
                download_path = get_ancillary(instrument, "ialirt-calibration")
                ialirt_calibration_data = load_cdf(download_path)

                logger.info("mag ialirt-calibration: %s", download_path)
                download_path = get_ancillary(instrument, "l1b-calibration")
                logger.info("mag l1b-calibration: %s", download_path)
                l1b_calibration_data = load_cdf(download_path)
                result = process_func(
                    combined,
                    l1b_calibration_data,
                    ialirt_calibration_data,
                )
            elif instrument == "codice_lo":
                logger.info("Processing CoDICE-Lo.")
                l1a_download_path = get_ancillary("codice", "l1a-sci-lut")
                # I-ALiRT Lo uses the same efficiency table as regular processing.
                l2_efficiency_download_path = get_ancillary(
                    "codice", "l2-lo-efficiency"
                )
                l2_geometric_download_path = get_ancillary("codice", "l2-lo-gfactor")
                # I-ALiRT Lo uses the same geometric factor table as regular processing.
                logger.info("codice l1a-sci-lut: %s", l1a_download_path)
                logger.info("codice l2-lo-efficiency: %s", l2_efficiency_download_path)
                logger.info("codice l2-lo-gfactor: %s", l2_geometric_download_path)
                result, _ = process_func(
                    combined,
                    l1a_download_path,
                    l2_efficiency_download_path,
                    "codice_lo",
                    l2_geometric_download_path,
                )
            elif instrument == "codice_hi":
                logger.info("Processing CoDICE-Hi.")
                l1a_download_path = get_ancillary("codice", "l1a-sci-lut")
                # I-ALiRT Hi uses its own efficiency table.
                l2_efficiency_download_path = get_ancillary(
                    "codice", "l2-hi-ialirt-efficiency"
                )
                logger.info("codice l1a-sci-lut: %s", l1a_download_path)
                logger.info(
                    "codice l2-hi-ialirt-efficiency: %s", l2_efficiency_download_path
                )
                # I-ALiRT Hi does not use a geometric factor.
                _, result = process_func(
                    combined,
                    l1a_download_path,
                    l2_efficiency_download_path,
                    "codice_hi",
                )
            elif instrument == "swapi":
                logger.info("Processing SWAPI.")
                download_path = get_ancillary(instrument, "esa-unit-conversion")
                logger.info("swapi esa-unit-conversion: %s", download_path)
                calibration_data = pd.read_csv(download_path)
                result = process_func(combined, calibration_data)
            else:
                logger.info("Processing HIT.")
                result = process_func(combined)

            if not result:
                logger.info("Empty result for: %s", instrument)
            else:
                logger.info("Data populated for: %s", instrument)

            if any(result) and all(result):
                if table_name == "ialirt-algorithm-table":
                    insert_data(result, algorithm_table, instrument, kernel_set_key)
                else:
                    insert_formatted_data(
                        result, algorithm_table, instrument, kernel_set_key
                    )

        except Exception as e:
            error_msg = f"Error processing {instrument}: {e!s}"
            logger.error(error_msg, exc_info=True)
            processing_errors.append((instrument, e))
            # Continue to next instrument


def insert_data(
    data: list[dict], algorithm_table, instrument: str, kernel_set_key: str
):
    """Insert or update database row, depending on content of item.

    Parameters
    ----------
    data : list[dict]
        Data product produced from processing respectively instrument.
    algorithm_table : dynamodb.Table
        The DynamoDB table to insert or update the data.
    instrument : str
        The prefix for the product name.
    kernel_set_key : str
        The kernel set identifier.
    """
    apid = data[0]["apid"]
    mets = [item["met"] for item in data]
    min_met = min(mets)
    max_met = max(mets)
    logger.info(f"Processing mets {min_met} to {max_met}.")
    logger.info(f"Processing utc {met_to_utc(min_met)} to {met_to_utc(max_met)}.")

    # Query existing items.
    response = algorithm_table.query(
        KeyConditionExpression=Key("apid").eq(apid)
        & Key("met").between(min_met, max_met)
    )

    existing_items = {item["met"]: item for item in response.get("Items", [])}

    # Insert or update as needed
    for raw in data:
        met = raw["met"]
        key = {"apid": apid, "met": met}
        existing = existing_items.get(met)
        raw["last_modified"] = datetime.now(timezone.utc).isoformat()
        raw["kernel_set_key"] = kernel_set_key

        # Calculate the spacecraft position and velocity in GSM coordinates.
        et = sct_to_et(met_to_sclkticks(met))
        gsm_state = imap_state(
            [et], ref_frame=SpiceFrame.IMAP_GSM, observer=SpiceBody.EARTH
        )
        gse_state = imap_state(
            [et], ref_frame=SpiceFrame.IMAP_GSE, observer=SpiceBody.EARTH
        )

        raw["sc_position_GSM"] = [Decimal(str(val)) for val in gsm_state[0, :3]]
        raw["sc_velocity_GSM"] = [Decimal(str(val)) for val in gsm_state[0, 3:]]
        raw["sc_position_GSE"] = [Decimal(str(val)) for val in gse_state[0, :3]]
        raw["sc_velocity_GSE"] = [Decimal(str(val)) for val in gse_state[0, 3:]]

        if existing:
            if any(key.startswith(instrument) for key in existing.keys()):
                continue

            update_expr = "SET " + ", ".join(
                f"{field} = :{field}"
                for field in raw
                if field
                not in {
                    "apid",
                    "met",
                    "met_in_utc",
                    "ttj2000ns",
                    "sc_position_GSM",
                    "sc_velocity_GSM",
                    "sc_position_GSE",
                    "sc_velocity_GSE",
                    "instrument",
                    "kernel_set_key",
                }
            )

            expression_values = {
                f":{field}": value
                for field, value in raw.items()
                if field
                not in {
                    "apid",
                    "met",
                    "met_in_utc",
                    "ttj2000ns",
                    "sc_position_GSM",
                    "sc_velocity_GSM",
                    "sc_position_GSE",
                    "sc_velocity_GSE",
                    "instrument",
                    "kernel_set_key",
                }
            }

            algorithm_table.update_item(
                Key=key,
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expression_values,
            )
        else:
            algorithm_table.put_item(Item=raw)
        logger.info(f"Inserted {instrument.upper()}.")


def reformat_data(data):
    """Reformat science and housekeeping data.

    Parameters
    ----------
    data : list[dict]
        Data product.

    Returns
    -------
    science_data : list[dict]
        Reformatted science data.
    hk_data : list[dict]
        Reformatted housekeeping data.
    """
    # Reformat data (remove all keep/exclude keys except hk
    # once imap_processing is updated)
    exclude_keys = {"apid", "met", "mag_hk_status"}
    rename_map = {"met_in_utc": "time_utc"}
    science_data = [
        {rename_map.get(k, k): v for k, v in item.items() if k not in exclude_keys}
        for item in data
    ]
    keep_keys = {"met_in_utc", "instrument", "mag_hk_status"}
    hk_data = [
        {
            rename_map.get(k, k): ("mag_hk" if k == "instrument" and v == "mag" else v)
            for k, v in item.items()
            if k in keep_keys
        }
        for item in data
        if item.get("instrument") == "mag"
    ]

    return science_data, hk_data


def insert_formatted_data(
    data: list[dict],
    data_table,
    instrument: str,
    kernel_set_key: str,
):
    """Insert database rows.

    Parameters
    ----------
    data : list[dict]
        Data product produced from processing respectively instrument.
    data_table : dynamodb.Table
        The DynamoDB table to insert or update the data.
    instrument : str
        The prefix for the product name.
    kernel_set_key : str
        The kernel set identifier.
    """
    # Get time range.
    times = [item["met_in_utc"] for item in data]
    min_time = min(times)
    max_time = max(times)
    logger.info(f"Processing {min_time} to {max_time} for {instrument}.")

    science_data, hk_data = reformat_data(data)

    # Insert science data
    for record in science_data:
        record["kernel_set_key"] = kernel_set_key
        data_table.put_item(Item=record)
    logger.info(f"Inserted {instrument.upper()}.")

    # Insert hk data
    if hk_data:
        for record in hk_data:
            record["kernel_set_key"] = kernel_set_key
            data_table.put_item(Item=record)
    logger.info(f"Inserted Housekeeping for {instrument.upper()}.")

    # Calculate the spacecraft position and velocity in GSE/GSM coordinates.
    et = str_to_et(min_time)
    gsm_state = imap_state(
        [et], ref_frame=SpiceFrame.IMAP_GSM, observer=SpiceBody.EARTH
    )
    gse_state = imap_state(
        [et], ref_frame=SpiceFrame.IMAP_GSE, observer=SpiceBody.EARTH
    )
    spacecraft = {
        "instrument": "spacecraft",
        "time_utc": min_time,
        "ttj2000ns": int(et_to_ttj2000ns(et)),
        "sc_position_GSM": [Decimal(str(val)) for val in gsm_state[0, :3]],
        "sc_velocity_GSM": [Decimal(str(val)) for val in gsm_state[0, 3:]],
        "sc_position_GSE": [Decimal(str(val)) for val in gse_state[0, :3]],
        "sc_velocity_GSE": [Decimal(str(val)) for val in gse_state[0, 3:]],
    }

    # Insert geolocation data
    data_table.put_item(Item=spacecraft)


def insert_kernels(dependency_inputs, algorithm_table):
    """Insert SPICE kernel metadata into the database.

    Parameters
    ----------
    dependency_inputs : ProcessingInputCollection
        SPICE kernel dependencies.
    algorithm_table : dynamodb.Table
        The DynamoDB table to insert or update the data.

    Returns
    -------
    kernel_set_key : str
        The kernel set identifier.
    """
    last_modified = datetime.now(timezone.utc)
    last_modified_for_spice = last_modified.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    met = et_to_met(str_to_et(last_modified_for_spice))
    last_modified_utc = last_modified.isoformat()
    spice_input = dependency_inputs.processing_input[0]
    spice_kernels = dict(zip(spice_input.source, spice_input.filename_list))

    # Will return the same kernel_set_key for the same set of kernels.
    kernel_set_key = met_to_utc(met).split(".")[0]

    if algorithm_table.table_name == "ialirt-algorithm-table":
        kernel_item = {
            "apid": 478,
            "met": int(met),
            "met_in_utc": met_to_utc(met).split(".")[0],
            "ttj2000ns": int(met_to_ttj2000ns(met)),
            "instrument": "spice",
            "last_modified": last_modified_utc,
            "spice_kernels": spice_kernels,
        }
    else:
        kernel_item = {
            "instrument": "spice",
            "time_utc": met_to_utc(met).split(".")[0],
            "spice_kernels": spice_kernels,
            "ttj2000ns": int(met_to_ttj2000ns(met)),
        }

    algorithm_table.put_item(Item=kernel_item)

    logger.info(
        f"Stored SPICE kernel mapping in "
        f"DynamoDB: {json.dumps(spice_kernels, indent=2)}"
    )
    return kernel_set_key


def lambda_handler(event, context):
    """Create metadata and add it to the database.

    This function is an event handler for s3 ingest bucket.
    It is also used to ingest data to the DynamoDB table.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    """
    logger.info("Received event: %s", json.dumps(event))

    algorithm_table_name = os.environ.get("ALGORITHM_TABLE")
    data_table_name = os.environ.get("DATA_TABLE")
    dynamodb = boto3.resource("dynamodb")
    algorithm_table = dynamodb.Table(algorithm_table_name)
    data_table = dynamodb.Table(data_table_name)
    url = os.environ.get("IMAP_DATA_ACCESS_URL")

    bucket = event["detail"]["bucket"]["name"]
    region = event["region"]

    s3_filepath = event["detail"]["object"]["key"]
    filename = os.path.basename(s3_filepath)
    logger.info("Retrieved filename: %s", filename)
    dependency_inputs = get_latest_spice_kernels(url)
    logger.info("dependency_inputs: %s", dependency_inputs)
    download_spice_file(dependency_inputs)

    # Query s3 for packet filenames from past 5 minutes.
    if "now" in event:
        now = datetime.fromisoformat(event["now"].replace("Z", "")).replace(
            tzinfo=timezone.utc
        )
    else:
        now = datetime.now(timezone.utc)
    filenames = query_filenames(bucket, region, now)

    if filenames:
        logger.info("Found %d files to process", len(filenames))
        logger.info(f"Parsing packets: {filenames}")
        # Get packets into datasets and combine.
        combined = parse_packets(filenames, bucket, Path("/tmp"))  # noqa: S108
        logger.info("Packets parsed. Processing algorithms.")
        # Insert kernel metadata every minute.
        kernel_set_key = insert_kernels(dependency_inputs, algorithm_table)
        # Process algorithms and insert new data.
        process_algorithms(
            combined, algorithm_table, algorithm_table_name, kernel_set_key
        )

        kernel_set_key = insert_kernels(dependency_inputs, data_table)
        process_algorithms(combined, data_table, data_table_name, kernel_set_key)

        logger.info("Successfully wrote all new items to DynamoDB")
    else:
        logger.info("No files found to process in the last 5 minutes.")
