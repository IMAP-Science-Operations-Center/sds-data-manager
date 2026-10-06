"""Utilities for processing I-ALiRT SSR data."""

import logging
from datetime import datetime, timedelta
from pathlib import Path

import imap_data_access
import requests
import xarray as xr
from imap_data_access.processing_input import (
    ProcessingInputCollection,
    SPICEInput,
    SPICESource,
)
from imap_processing import imap_module_directory
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


def get_latest_spice_kernels(url: str, day: datetime) -> ProcessingInputCollection:
    """Query the SPICE metakernel API for the latest kernels for a day.

    The time range covers the day and the day after, to match the packet files.

    Parameters
    ----------
    url : str
        The IMAP data access API URL.
    day : datetime
        The day to query.

    Returns
    -------
    dependency_inputs : ProcessingInputCollection
        A collection containing a SPICEInput object with the list of kernel
        filenames returned from the metakernel API, in priority order.
    """
    # The end date is exclusive (midnight), so day + 2 covers day and day + 1.
    metakernel_url = url + "/metakernel"
    params = {
        "start_time": day.strftime("%Y%m%d"),
        "end_time": (day + timedelta(days=2)).strftime("%Y%m%d"),
        "list_files": "True",
        "file_types": ",".join(KERNELS),
    }

    logger.info(f"Sending request to {metakernel_url} with params: {params}")
    response = requests.get(metakernel_url, params=params, timeout=10)
    response.raise_for_status()
    metakernel_files = response.json()

    logger.info(f"Found metakernel files: {metakernel_files}. Adding to collection.")
    dependency_inputs = ProcessingInputCollection()
    dependency_inputs.add(SPICEInput(*metakernel_files))

    return dependency_inputs


def download_spice_files(dependencies: ProcessingInputCollection) -> list[Path]:
    """Download SPICE kernel files from the IMAP data archive to EFS.

    Parameters
    ----------
    dependencies : ProcessingInputCollection
        A collection containing a SPICEInput object with the list of kernel
        filenames returned from the metakernel API.

    Returns
    -------
    spice_files : list[Path]
        The SPICE files stored in EFS.

    Notes
    -----
    List is priority ordered so furnishing in order results in correct SPICE priority.
    """
    imap_data_access.config["DATA_DIR"] = EFS_BASE_PATH
    dependencies.download_all_files()

    spice_files = dependencies.get_file_paths(data_type=SPICESource.SPICE.value)
    logger.info(f"Downloaded SPICE files: {spice_files}")

    return spice_files
