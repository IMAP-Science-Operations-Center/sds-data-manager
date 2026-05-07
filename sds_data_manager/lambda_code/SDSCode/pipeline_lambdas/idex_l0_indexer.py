"""Functions for supporting the idex l0 indexer component of the architecture."""

import json
import logging
import os

import boto3
import numpy as np
import pandas as pd
from imap_data_access import ImapFilePath
from imap_processing.idex.idex_constants import IDEXAPID
from imap_processing.idex.idex_l0 import decom_packets
from imap_processing.idex.idex_l1a import Scitype
from imap_processing.spice.time import met_to_datetime64

from ..spice_utilities import download_from_s3, furnish_best_spice_file
from .indexer import http_response, write_file_metadata_to_table

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def compute_idex_lo_event_times(s3_filepath: str) -> np.ndarray:
    """Compute idex l0 event times for science and event packets."""
    packet_file = download_from_s3(s3_filepath)
    science_packets, raw_datset_by_apid, _ = decom_packets(packet_file)
    event_times = []
    if science_packets:
        for packet in science_packets:
            if "IDX__SCI0TYPE" in packet:
                scitype = packet["IDX__SCI0TYPE"]
                if scitype == Scitype.FIRST_PACKET:
                    # These header packet values make up the IDEX coarse event time.
                    shcoarse = (packet["IDX__TXHDRTIMESEC1"] << 16) + packet[
                        "IDX__TXHDRTIMESEC2"
                    ]
                    event_times.append(shcoarse)
    # Get Event message times
    if IDEXAPID.IDEX_EVT in raw_datset_by_apid:
        shcoarse = raw_datset_by_apid[IDEXAPID.IDEX_EVT]["elsec_evtpkt"].values
        event_times.extend(shcoarse)
    event_times = np.unique(np.asarray(event_times))
    return event_times


def compute_idex_l0_start_dates(s3_filepath: str):
    """Download and compute the start dates for the l0 file."""
    event_times = compute_idex_lo_event_times(s3_filepath)
    if len(event_times) == 0:
        logger.warning("No l0 events found for this file")
        return []
    # convert event times to datetime
    event_times = met_to_datetime64(event_times)
    # Load the csv that outlines the start and end date of each file.
    # All IDEX products will be organized into files that span 10 days.
    # This lookup table was provided by the IDEX instrument team.
    idex_10_day_ranges = pd.read_csv(
        "sds_data_manager/utils/idex_10_day_CDF_names.csv", header=0
    )
    # Get start dates as numpy datetime64 arrays.
    start_dates = pd.to_datetime(
        idex_10_day_ranges["start_date"], format="%Y%m%d"
    ).to_numpy(dtype="datetime64[ns]")
    end_dates = pd.to_datetime(
        idex_10_day_ranges["end_date"], format="%Y%m%d"
    ).to_numpy(dtype="datetime64[ns]")
    # Make sure dates are sorted in ascending order
    start_dates = np.sort(start_dates)

    # Check for any dates out of range. If event time is before the first start date
    # or after the last end date, raise an error. We should have dates for the full
    # mission
    if np.any(event_times < start_dates) or np.any(event_times > end_dates):
        raise ValueError(
            "There are event out of range for the IDEX CDF naming "
            "convention. Check that the idex_10_day_CDF_names.csv file has the correct "
            f"date ranges and covers the full mission. Event times: "
            f"{event_times}. File naming date range: {np.min(start_dates)}"
            f" - {np.max(end_dates)}"
        )
    # For each event time, find the latest 10 day range where event time >= the range
    # start date.
    range_idx = np.searchsorted(start_dates, event_times, side="right") - 1

    # Return unique 10-day file start dates represented by this L0 file.
    return np.unique(start_dates[range_idx])


def idex_l0_s3_event_handler(event):
    """S3 events handler.

    S3 event handler takes idex l0 s3 events and then writes information to
    the proper file table.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process

    Returns
    -------
    dict
        HTTP response

    """
    # Retrieve the Object name
    s3_filepath = event["detail"]["object"]["key"]

    filename = os.path.basename(s3_filepath)
    try:
        _, _ = write_file_metadata_to_table(filename, s3_filepath)
    except ImapFilePath.InvalidImapFileError:
        return http_response(
            status_code=400,
            body=f"Filename {filename} is not a valid SCIENCE, "
            + "ANCILLARY or QUICKLOOK file.",
        )
    # In addition to populating the science files table with the file metadata,
    # compute the raw idex file
    # start_dates = compute_idex_l0_start_dates(s3_filepath)
    # with db.Session() as session, session.begin():
    #     science_file = models.IDEXLOFiles(**sci_params)
    #     session.add(science_file)
    #     crid = calculate_crid(session, science_file)
    #     science_file.crid = crid
    # Send event from this lambda for Batch starter lambda
    logger.debug("S3 IDEX l0 event handler complete")
    return http_response(status_code=200, body="Success")


def lambda_handler(event, context):
    """Create metadata and add it to the database.

    This function is an event handler for multiple event sources.
    List of event sources are aws.s3, aws.batch and imap.lambda.
    imap.lambda is custom PutEvent from AWS lambda.

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
    logger.info("Received event: " + json.dumps(event, indent=2))
    source = event.get("source")

    if source == "aws.s3":
        # Furnish spice kernels
        try:
            _ = furnish_best_spice_file("leapseconds")
            _ = furnish_best_spice_file("spacecraft_clock")
        except FileNotFoundError as e:
            logger.error(f"Error furnishing SPICE kernels: {e}")
            return http_response(
                status_code=500, body=f"Error furnishing SPICE kernels: {e}"
            )
        return idex_l0_s3_event_handler(event)
    else:
        logger.error("Unknown event source")
        return http_response(status_code=400, body="Unknown event source")
