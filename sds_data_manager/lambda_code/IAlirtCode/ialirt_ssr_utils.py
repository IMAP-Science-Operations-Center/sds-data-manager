"""Utilities for processing I-ALiRT SSR data."""

import logging
from datetime import datetime, timedelta
from pathlib import Path

import imap_data_access
import xarray as xr
from imap_processing import imap_module_directory
from imap_processing.utils import packet_file_to_datasets

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def query_filenames(day: datetime) -> list[str]:
    """Query the latest I-ALiRT L0 files for a given day and the day after.

    Packets for a given day can land in the next day's file, so both days
    are needed to get a complete day of data.

    Parameters
    ----------
    day : datetime
        The day to query.

    Returns
    -------
    filenames : list[str]
        File paths relative to the data archive root, one per day.

    Raises
    ------
    FileNotFoundError
        If a file is missing for either day.
    """
    dates = [day.strftime("%Y%m%d"), (day + timedelta(days=1)).strftime("%Y%m%d")]
    results = imap_data_access.query(
        table="science",
        instrument="ialirt",
        data_level="l0",
        descriptor="raw",
        start_date=dates[0],
        end_date=dates[1],
        version="latest",
    )

    found_dates = {result["start_date"] for result in results}
    missing_dates = [date for date in dates if date not in found_dates]
    if missing_dates:
        raise FileNotFoundError(f"No I-ALiRT L0 file found for {missing_dates}")

    filenames = [result["file_path"] for result in results]
    logger.info(f"Found I-ALiRT L0 files for {dates}: {filenames}")

    return filenames


def parse_packets(
    filenames: list[str], download_dir: Path, apid: int = 478
) -> xr.Dataset:
    """Download I-ALiRT L0 files from the archive and combine into a dataset.

    Parameters
    ----------
    filenames : list[str]
        File paths relative to the data archive root.
    download_dir : Path
        The directory where the files will be downloaded.
    apid : int
        The apid of the packet to be processed.

    Returns
    -------
    combined : xr.Dataset
        Combined dataset.
    """
    imap_data_access.config["DATA_DIR"] = download_dir
    xtce_ialirt_path = (
        imap_module_directory / "ialirt" / "packet_definitions" / "ialirt.xml"
    )

    xarray_data = tuple(
        packet_file_to_datasets(
            imap_data_access.download(filename),
            xtce_ialirt_path,
            use_derived_value=False,
        )[apid]
        for filename in filenames
    )

    combined = xr.concat(xarray_data, dim="epoch").sortby("epoch")

    # The same packet may be in both files. Drop packets where epoch and
    # src_seq_ctr are the same, as is done within packet_file_to_datasets.
    unique_indices = (
        combined[["src_seq_ctr"]]
        .to_dataframe()
        .reset_index()
        .drop_duplicates()
        .index.values
    )
    combined = combined.isel(epoch=unique_indices)

    return combined
