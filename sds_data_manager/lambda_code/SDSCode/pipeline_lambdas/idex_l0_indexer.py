"""IDEX L0 indexer support code.

IDEX L0 files are named by packet creation time (shcoarse), but the actual
event times are in another variable in the packets. Since a single downlink may
contain events from different time periods, we can't trust
the filename. This module reads the event times out of each L0 file and
figures out which 10-day windows those events actually belong to, then
writes that mapping to the database so downstream handling can gather the correct l0
files for each 10-day window start date.

For example the idex l0 database will look something like this:

file_path                                start_date  version ingestion_date
...imap_idex_l0_raw_20260211_v001.pkts    2026-02-09   v001    2026-05-11
...imap_idex_l0_raw_20260215_v002.pkts    2026-02-09   v002    2026-05-11
...imap_idex_l0_raw_20260212_v002.pkts    2026-02-09   v002    2026-05-11
...imap_idex_l0_raw_20260213_v002.pkts    2026-02-09   v002    2026-05-11
...imap_idex_l0_raw_20260214_v001.pkts    2026-02-09   v001    2026-05-11
...imap_idex_l0_raw_20260218_v001.pkts    2026-02-09   v001    2026-05-11
...imap_idex_l0_raw_20260219_v001.pkts    2026-02-09   v001    2026-05-11
...imap_idex_l0_raw_20260223_v003.pkts    2026-02-09   v003    2026-05-11
...imap_idex_l0_raw_20260216_v002.pkts    2026-02-09   v002    2026-05-11
...imap_idex_l0_raw_20260217_v002.pkts    2026-02-09   v002    2026-05-11
...imap_idex_l0_raw_20260218_v001.pkts    2026-02-19   v001    2026-05-11


The start date corresponds to the start of a 10 day window. To find all the files
needed to process a given window, downstream code can query the database for all files
 with that start date.
"""

import logging
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from imap_data_access import ImapFilePath, ScienceFilePath
from imap_processing.idex.idex_constants import IDEX_10_DAY_RANGES_PATH, IDEXAPID
from imap_processing.idex.idex_l0 import decom_packets
from imap_processing.idex.idex_l1a import Scitype
from imap_processing.spice.time import met_to_datetime64

from ..database import database as db
from ..database import models
from ..spice_utilities import download_from_s3, furnish_best_spice_file
from .indexer import get_file_ingestion_date, http_response

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def write_science_metadata_to_table(filename: str, s3_filepath: str) -> dict:
    """Parse an IDEX L0 science filename and insert metadata into ScienceFiles.

    Parameters
    ----------
    filename : str
        Basename of the science file (for example,
        ``imap_idex_l0_raw_20240101_v001.pkts``).
    s3_filepath : str
        Full S3 object key for the file.

    Returns
    -------
    dict
        Metadata dictionary written to ``models.ScienceFiles``. Includes
        parsed filename fields plus ``file_path`` and ``ingestion_date``.

    Raises
    ------
    ImapFilePath.InvalidImapFileError
        If ``filename`` does not conform to the IMAP science filename format.
    ValueError
        If parsing returns a non-science file object.
    """
    file_obj = ScienceFilePath(filename)
    params = file_obj.extract_filename_components(filename)
    # Extract filename components and prepare common parameters for
    # database entry.
    params.pop("mission")
    params["start_date"] = datetime.strptime(params.pop("start_date"), "%Y%m%d")
    params["file_path"] = s3_filepath
    params["ingestion_date"] = get_file_ingestion_date(s3_filepath)

    with db.Session() as session, session.begin():
        science_file = models.ScienceFiles(**params)
        session.add(science_file)
        crid = None
        science_file.crid = crid

    logger.info("Wrote data to the ScienceFiles table")

    return params


def compute_idex_l0_event_times(s3_filepath: str) -> np.ndarray:
    """Pull all IDEX event times out of a single L0 file.

    We grab event times from two places — science packets (which require
    reconstructing the coarse time from two header fields) and event message
    packets (which have it directly). Both get lumped together and returned
    as a flat array.

    Parameters
    ----------
    s3_filepath : str
        S3 object key for the IDEX L0 file.

    Returns
    -------
    np.ndarray
        All event times found in the file converted to datetime64.
    """
    packet_file = download_from_s3(s3_filepath)
    science_packets, raw_datset_by_apid, _ = decom_packets(packet_file)
    event_times = []

    if science_packets:
        for packet in science_packets:
            if "IDX__SCI0TYPE" in packet:
                scitype = packet["IDX__SCI0TYPE"]
                if scitype == Scitype.FIRST_PACKET:
                    # Coarse event time is split across two header fields,
                    # shift the high word and OR them together to reconstruct it.
                    event_time = (packet["IDX__TXHDRTIMESEC1"] << 16) + packet[
                        "IDX__TXHDRTIMESEC2"
                    ]
                    event_times.append(event_time)

    # Event message packets store shcoarse directly, no reconstruction needed.
    if IDEXAPID.IDEX_EVT in raw_datset_by_apid:
        event_times.extend(raw_datset_by_apid[IDEXAPID.IDEX_EVT]["elsec_evtpkt"].values)
    # convert to datetime64 for further processing
    return met_to_datetime64(np.asarray(event_times))


def compute_idex_l0_start_dates(s3_filepath: str) -> list:
    """Map an L0 file to the 10-day windows its events fall into.

    One L0 file can span multiple windows if it contains events from
    different time periods (e.g. a late downlink carrying old data).
    We convert event times from MET to datetime, then use a mission-provided
    lookup table to figure out which window each event belongs to.

    Parameters
    ----------
    s3_filepath : str
        S3 object key for the IDEX L0 file.

    Returns
    -------
     list
        Unique window start dates touched by this file. Empty list if the
        file has no IDEX events.
    """
    event_times = compute_idex_l0_event_times(s3_filepath)
    if len(event_times) == 0:
        logger.warning(
            f"No IDEX events found in {s3_filepath}, skipping window calculation"
        )
        return []

    # IDEX instrument team defined 10-day window boundaries. Each row is one window with
    # a start and end date.
    if not Path(IDEX_10_DAY_RANGES_PATH).exists():
        raise FileNotFoundError(
            f"Unable to find IDEX window definition CSV at {IDEX_10_DAY_RANGES_PATH}"
        )

    idex_10_day_ranges = pd.read_csv(IDEX_10_DAY_RANGES_PATH, header=0)
    start_dates = np.sort(
        pd.to_datetime(idex_10_day_ranges["start_date"], format="%Y%m%d")
    )
    end_dates = pd.to_datetime(idex_10_day_ranges["end_date"], format="%Y%m%d")

    # Check if any event falls outside the mission window range something is wrong with
    # either the data or the csv.
    start_range = np.min(start_dates)
    end_range = np.max(end_dates)
    if np.any(event_times < start_range) or np.any(event_times > end_range):
        # TODO raise a value error here instead of logging a warning. There is
        #   a known issue in some idex l0 event msg packets where MET == 1. The IDEX
        #   team is aware and will let us know how to proceed.
        logger.warning(
            f"Event times fall outside the mission window range defined in "
            f"idex_10_day_CDF_names.csv. Event times: {event_times}. "
            f"Mission window range: {start_range} - {end_range}. "
            f"The CSV may need to be extended."
        )

    # For each event, find the latest window start date that is <= the event time.
    window_idx = np.searchsorted(start_dates, event_times, side="right") - 1

    # TODO remove this filter once the TODO above is removed
    # Filter for window indices that are valid e.g. not 0 or len(start_dates)
    window_idx = window_idx[(window_idx >= 0) & (window_idx < len(start_dates))]

    return list(np.unique(start_dates[window_idx]))


def lambda_handler(event, context):
    """Handle an incoming IDEX L0 file and write metadata to the database.

    Beyond the standard file metadata that every L0 file gets, we also write
    one row per 10-day window into the IDEX-specific table.

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
    logger.info(f"Indexing IDEX L0 file from event {event}")
    s3_filepath = event["detail"]["object"]["key"]
    filename = os.path.basename(s3_filepath)

    # Furnish spice kernels
    logger.info(
        "Gathering leapsecond and spacecraft clock kernels for reading"
        " event times from IDEX L0 needed for indexing of file"
        f" {filename} to IDEX database table"
    )
    # add metadata to science file table.
    try:
        params = write_science_metadata_to_table(filename, s3_filepath)
    except ImapFilePath.InvalidImapFileError:
        return http_response(
            status_code=400,
            body=f"Filename {filename} is not a valid SCIENCE file.",
        )

    try:
        _ = furnish_best_spice_file("leapseconds")
        _ = furnish_best_spice_file("spacecraft_clock")
    except FileNotFoundError as e:
        logger.error(f"Error furnishing SPICE kernels: {e}")
        return http_response(
            status_code=500, body=f"Error furnishing SPICE kernels: {e}"
        )

    # Figure out which 10-day windows this file touches and write one DB row per window.
    # A single file can touch multiple windows if it contains events from different
    # periods.
    logger.info(
        "Computing which 10-day windows this IDEX L0 file belongs to based "
        "on event times"
    )
    start_dates = compute_idex_l0_start_dates(s3_filepath)
    logger.info(
        f"File {filename} belongs to windows starting on these dates: {start_dates}"
    )
    idex_params = {
        "ingestion_date": params["ingestion_date"],
        "major_version": params["major_version"],
        "minor_version": params["minor_version"],
        "file_path": params["file_path"],
    }
    logger.info(f"Writing metadata to idex-l0-files db table: {idex_params}")
    with db.Session() as session, session.begin():
        for start_date in start_dates:
            idex_params["start_date"] = pd.Timestamp(start_date)  # .to_pydatetime()
            session.add(models.IDEXL0Files(**idex_params))

    logger.debug(f"IDEX L0 indexing complete for {filename}")
    return http_response(status_code=200, body="Success")
