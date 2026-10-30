"""I-ALiRT SSR processing lambdas.

Each instrument (and the spacecraft) is deployed as its own Lambda from the
same image. The INSTRUMENT environment variable selects which processor runs.
"""

import json
import logging
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib.metadata import version as package_version
from pathlib import Path

import boto3
import imap_data_access
import numpy as np
import pandas as pd
import spiceypy
import xarray as xr
from ialirt_ssr_utils import (
    KERNELS,
    download_spice_files,
    get_ancillary,
    get_latest_spice_kernels,
    parse_packets,
    query_filenames,
)
from imap_data_access.processing_input import ProcessingInputCollection
from imap_processing.cdf.utils import load_cdf
from imap_processing.ialirt.l0.parse_mag import process_packet
from imap_processing.ialirt.l0.process_codice import process_codice
from imap_processing.ialirt.l0.process_hit import process_hit
from imap_processing.ialirt.l0.process_status import process_status
from imap_processing.ialirt.l0.process_swapi import process_swapi_ialirt
from imap_processing.ialirt.l0.process_swe import process_swe
from imap_processing.spice.geometry import SpiceBody, SpiceFrame, imap_state
from imap_processing.spice.time import (
    et_to_ttj2000ns,
    et_to_utc,
    str_to_et,
    str_yyyymmdd_to_ttj2000ns,
    ttj2000ns_to_et,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Only MAG and the spacecraft use SPICE geometry. The other instruments only
# convert times, which needs just these kernels.
GEOMETRY_INSTRUMENTS = {"mag", "spacecraft"}
TIME_KERNELS = {"leapseconds", "spacecraft_clock"}
# Spacecraft position/velocity cadence over the day.
SPACECRAFT_CADENCE = timedelta(minutes=1)
# Packets, SPICE kernels and ancillary files for one invocation.
DOWNLOAD_DIR = Path("/tmp/ssr")  # noqa: S108
# The two values below do different jobs:
# - COVERAGE_TOLERANCE is the pass/fail completeness check in
#   check_day_coverage. The first packet must be in the first 60 s of the day,
#   and there must be a packet in the first 60 s of the next day to show the
#   data continues through the end of the day.
# - BOUNDARY_BUFFER is used by trim_packets to decide how much data is passed
#   to the instrument processing. It is larger because some products are built
#   from packets spread over 4 minutes, so a product near midnight needs
#   packets from the neighboring day.
COVERAGE_TOLERANCE = timedelta(seconds=60)
BOUNDARY_BUFFER = timedelta(minutes=10)
# src_seq_ctr is a 14-bit counter.
SEQ_CTR_MODULO = 16384
# How often to store spacecraft status. Matches ialirt-ingest's ~1 per minute.
STATUS_CADENCE_NS = 60 * 10**9
SOFTWARE_VERSIONS = {
    "imap_processing": package_version("imap-processing"),
    "imap_data_access": package_version("imap-data-access"),
}


class IalirtDataGapError(Exception):
    """Raised when a day of I-ALiRT packets has gaps and is not processed."""


def ttj2000ns_to_utc(ttj2000ns: int) -> str:
    """Convert TTJ2000 nanoseconds (packet epoch) to a UTC string."""
    return et_to_utc(ttj2000ns_to_et(ttj2000ns))


def check_day_coverage(combined: xr.Dataset, day: datetime) -> None:
    """Check that the packets cover the whole day with no missing packets.

    Days that fail are skipped and need to be inspected and processed
    manually.

    Parameters
    ----------
    combined : xr.Dataset
        L0 parsed data for the day before, the day and the day after,
        sorted by epoch.
    day : datetime
        The day being processed.

    Raises
    ------
    IalirtDataGapError
        If the first packet is not near the start of the day, the last packet
        is not at or after the end of the day, or src_seq_ctr skips a value.
    """
    tolerance = int(COVERAGE_TOLERANCE.total_seconds() * 1e9)
    day_start = str_yyyymmdd_to_ttj2000ns(f"{day:%Y%m%d}")
    day_end = str_yyyymmdd_to_ttj2000ns(f"{day + timedelta(days=1):%Y%m%d}")

    # Include packets just after midnight to show the data continues
    # through the end of the day.
    packets = combined.sel(epoch=slice(day_start, day_end + tolerance))
    epoch = packets["epoch"].values
    src_seq_ctr = packets["src_seq_ctr"].values.astype(np.int64)

    if epoch.size == 0:
        raise IalirtDataGapError(f"{day:%Y%m%d}: no packets.")
    if epoch[0] > day_start + tolerance:
        raise IalirtDataGapError(
            f"{day:%Y%m%d}: first packet at {ttj2000ns_to_utc(epoch[0])}."
        )
    if epoch[-1] < day_end:
        raise IalirtDataGapError(
            f"{day:%Y%m%d}: last packet at {ttj2000ns_to_utc(epoch[-1])}."
        )

    gaps = np.flatnonzero(np.diff(src_seq_ctr) % SEQ_CTR_MODULO != 1)
    if gaps.size:
        raise IalirtDataGapError(
            f"{day:%Y%m%d}: {gaps.size} src_seq_ctr gap(s), "
            f"first after {ttj2000ns_to_utc(epoch[gaps[0]])}."
        )


def trim_packets(combined: xr.Dataset, day: datetime) -> xr.Dataset:
    """Keep only the packets needed to process the day.

    combined has the whole day before, the day and the day after. Only the
    day plus BOUNDARY_BUFFER on each side is needed, so products near
    midnight still have all of their packets.

    Parameters
    ----------
    combined : xr.Dataset
        L0 parsed data for the day before, the day and the day after,
        sorted by epoch.
    day : datetime
        The day being processed.

    Returns
    -------
    xr.Dataset
        Packets from BOUNDARY_BUFFER before the day to BOUNDARY_BUFFER after.
    """
    buffer = int(BOUNDARY_BUFFER.total_seconds() * 1e9)
    day_start = str_yyyymmdd_to_ttj2000ns(f"{day:%Y%m%d}")
    day_end = str_yyyymmdd_to_ttj2000ns(f"{day + timedelta(days=1):%Y%m%d}")
    return combined.sel(epoch=slice(day_start - buffer, day_end + buffer))


def process_mag(
    combined: xr.Dataset, day: datetime, download_dir: Path
) -> tuple[list[dict], dict]:
    """Process MAG.

    Parameters
    ----------
    combined : xr.Dataset
        L0 parsed data.
    day : datetime
        The day being processed.
    download_dir : Path
        The directory where ancillary files are downloaded.

    Returns
    -------
    result : list[dict]
        MAG data products.
    ancillary_files : dict
        Ancillary filenames used, keyed by instrument_descriptor.
    """
    ialirt_cal_path = get_ancillary("mag", "ialirt-calibration", day, download_dir)
    l1b_cal_path = get_ancillary("mag", "l1b-calibration", day, download_dir)
    result = process_packet(combined, load_cdf(l1b_cal_path), load_cdf(ialirt_cal_path))
    ancillary_files = {
        "mag_ialirt-calibration": ialirt_cal_path.name,
        "mag_l1b-calibration": l1b_cal_path.name,
    }
    return result, ancillary_files


def process_swe_ssr(
    combined: xr.Dataset, day: datetime, download_dir: Path
) -> tuple[list[dict], dict]:
    """Process SWE.

    Parameters
    ----------
    combined : xr.Dataset
        L0 parsed data.
    day : datetime
        The day being processed.
    download_dir : Path
        The directory where ancillary files are downloaded.

    Returns
    -------
    result : list[dict]
        SWE data products.
    ancillary_files : dict
        Ancillary filenames used, keyed by instrument_descriptor.
    """
    cal_path = get_ancillary("swe", "l1b-in-flight-cal", day, download_dir)
    result = process_swe(combined, [cal_path])
    return result, {"swe_l1b-in-flight-cal": cal_path.name}


def process_codice_lo(
    combined: xr.Dataset, day: datetime, download_dir: Path
) -> tuple[list[dict], dict]:
    """Process CoDICE-Lo.

    CoDICE-Lo uses the same efficiency and geometric factor tables as
    regular processing.

    Parameters
    ----------
    combined : xr.Dataset
        L0 parsed data.
    day : datetime
        The day being processed.
    download_dir : Path
        The directory where ancillary files are downloaded.

    Returns
    -------
    result : list[dict]
        CoDICE-Lo data products.
    ancillary_files : dict
        Ancillary filenames used, keyed by instrument_descriptor.
    """
    lut_path = get_ancillary("codice", "l1a-sci-lut", day, download_dir)
    efficiency_path = get_ancillary("codice", "l2-lo-efficiency", day, download_dir)
    gfactor_path = get_ancillary("codice", "l2-lo-gfactor", day, download_dir)
    result, _ = process_codice(
        combined, lut_path, efficiency_path, "codice_lo", gfactor_path
    )
    ancillary_files = {
        "codice_l1a-sci-lut": lut_path.name,
        "codice_l2-lo-efficiency": efficiency_path.name,
        "codice_l2-lo-gfactor": gfactor_path.name,
    }
    return result, ancillary_files


def process_codice_hi(
    combined: xr.Dataset, day: datetime, download_dir: Path
) -> tuple[list[dict], dict]:
    """Process CoDICE-Hi.

    CoDICE-Hi uses its own I-ALiRT efficiency table and no geometric factor.

    Parameters
    ----------
    combined : xr.Dataset
        L0 parsed data.
    day : datetime
        The day being processed.
    download_dir : Path
        The directory where ancillary files are downloaded.

    Returns
    -------
    result : list[dict]
        CoDICE-Hi data products.
    ancillary_files : dict
        Ancillary filenames used, keyed by instrument_descriptor.
    """
    lut_path = get_ancillary("codice", "l1a-sci-lut", day, download_dir)
    efficiency_path = get_ancillary(
        "codice", "l2-hi-ialirt-efficiency", day, download_dir
    )
    _, result = process_codice(combined, lut_path, efficiency_path, "codice_hi")
    ancillary_files = {
        "codice_l1a-sci-lut": lut_path.name,
        "codice_l2-hi-ialirt-efficiency": efficiency_path.name,
    }
    return result, ancillary_files


def process_swapi(
    combined: xr.Dataset, day: datetime, download_dir: Path
) -> tuple[list[dict], dict]:
    """Process SWAPI.

    Parameters
    ----------
    combined : xr.Dataset
        L0 parsed data.
    day : datetime
        The day being processed.
    download_dir : Path
        The directory where ancillary files are downloaded.

    Returns
    -------
    result : list[dict]
        SWAPI data products.
    ancillary_files : dict
        Ancillary filenames used, keyed by instrument_descriptor.
    """
    cal_path = get_ancillary("swapi", "esa-unit-conversion", day, download_dir)
    result = process_swapi_ialirt(combined, pd.read_csv(cal_path))
    return result, {"swapi_esa-unit-conversion": cal_path.name}


def process_spacecraft_status(combined: xr.Dataset) -> list[dict]:
    """Process spacecraft status, one product per minute.

    process_status gives every record the time of the middle of its input,
    so it is called on only the last packet of each minute. Each product
    then has that packet's status values and time.

    Parameters
    ----------
    combined : xr.Dataset
        L0 parsed data.

    Returns
    -------
    result : list[dict]
        Spacecraft status data products, one per minute.
    """
    minute = combined["epoch"] // STATUS_CADENCE_NS
    return [
        process_status(packets.isel(epoch=[-1]))[0]
        for _, packets in combined.groupby(minute)
    ]


def trim_to_day(result: list[dict], day: datetime) -> list[dict]:
    """Keep only products with met_in_utc within the day.

    Trimming happens after processing so that products built from packets
    near midnight still have all of their packets.

    Parameters
    ----------
    result : list[dict]
        Data products with a met_in_utc key.
    day : datetime
        The day being processed.

    Returns
    -------
    list[dict]
        Data products within [day, day + 1).
    """
    start = day.strftime("%Y-%m-%dT%H:%M:%S")
    end = (day + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    return [item for item in result if start <= item["met_in_utc"] < end]


def format_items(
    result: list[dict],
    instrument: str,
    version: str,
    kernel_set_key: str,
) -> list[dict]:
    """Convert data products into SSR table items.

    Parameters
    ----------
    result : list[dict]
        Data products from processing.
    instrument : str
        The instrument name.
    version : str
        The data version, e.g. "v001".
    kernel_set_key : str
        time_utc of the spice and ancillary items used for processing.

    Returns
    -------
    items : list[dict]
        Items keyed by instrument_version and time_utc.
    """
    exclude_keys = {"apid", "met", "met_in_utc", "mag_hk_status"}
    return [
        {
            "instrument_version": f"{instrument}#{version}",
            "time_utc": item["met_in_utc"],
            **{k: v for k, v in item.items() if k not in exclude_keys},
            "kernel_set_key": kernel_set_key,
        }
        for item in result
    ]


def mag_hk_items(result: list[dict], version: str, kernel_set_key: str) -> list[dict]:
    """Convert MAG data products into MAG housekeeping items.

    Parameters
    ----------
    result : list[dict]
        MAG data products from processing.
    version : str
        The data version, e.g. "v001".
    kernel_set_key : str
        time_utc of the spice and ancillary items used for processing.

    Returns
    -------
    items : list[dict]
        Items keyed by instrument_version and time_utc.
    """
    return [
        {
            "instrument_version": f"mag_hk#{version}",
            "time_utc": item["met_in_utc"],
            "instrument": "mag_hk",
            "mag_hk_status": item["mag_hk_status"],
            "kernel_set_key": kernel_set_key,
        }
        for item in result
    ]


def to_decimal(values: np.ndarray) -> list[Decimal]:
    """Convert floats to Decimal, since DynamoDB does not accept floats."""
    return [Decimal(str(val)) for val in values]


def spacecraft_items(day: datetime, version: str, kernel_set_key: str) -> list[dict]:
    """Calculate spacecraft position and velocity over the day.

    Parameters
    ----------
    day : datetime
        The day being processed.
    version : str
        The data version, e.g. "v001".
    kernel_set_key : str
        time_utc of the spice item used for processing.

    Returns
    -------
    items : list[dict]
        GSE/GSM position and velocity items at SPACECRAFT_CADENCE.
    """
    n_steps = int(timedelta(days=1) / SPACECRAFT_CADENCE)
    times = [day + i * SPACECRAFT_CADENCE for i in range(n_steps)]
    time_strs = [t.strftime("%Y-%m-%dT%H:%M:%S") for t in times]
    ets = np.array([str_to_et(t) for t in time_strs])

    gsm_state = imap_state(ets, ref_frame=SpiceFrame.IMAP_GSM, observer=SpiceBody.EARTH)
    gse_state = imap_state(ets, ref_frame=SpiceFrame.IMAP_GSE, observer=SpiceBody.EARTH)

    return [
        {
            "instrument_version": f"spacecraft#{version}",
            "time_utc": time_str,
            "instrument": "spacecraft",
            "ttj2000ns": int(et_to_ttj2000ns(et)),
            "sc_position_GSM": to_decimal(gsm[:3]),
            "sc_velocity_GSM": to_decimal(gsm[3:]),
            "sc_position_GSE": to_decimal(gse[:3]),
            "sc_velocity_GSE": to_decimal(gse[3:]),
            "kernel_set_key": kernel_set_key,
        }
        for time_str, et, gsm, gse in zip(
            time_strs, ets, gsm_state, gse_state, strict=True
        )
    ]


def spice_item(
    dependencies: ProcessingInputCollection,
    version: str,
    kernel_set_key: str,
    kernel_set_key_ttj2000ns: int,
) -> dict:
    """Create the item for the SPICE kernels used.

    Parameters
    ----------
    dependencies : ProcessingInputCollection
        A collection containing a SPICEInput object with the kernels used.
    version : str
        The data version, e.g. "v001".
    kernel_set_key : str
        The kernel set identifier, used as time_utc.
    kernel_set_key_ttj2000ns : int
        The kernel set key in TTJ2000 ns.

    Returns
    -------
    item : dict
        The spice item, keyed by kernel type.
    """
    spice_input = dependencies.processing_input[0]
    # All kernels are loaded, and more than one of a type can be returned
    # (e.g. two ephemeris files each covering part of the window), so keep
    # every filename per type, in load order.
    spice_kernels = {}
    for path, filename in zip(
        spice_input.imap_file_paths, spice_input.filename_list, strict=True
    ):
        kernel_type = path.spice_metadata["type"]
        if kernel_type not in spice_kernels:
            spice_kernels[kernel_type] = []
        spice_kernels[kernel_type].append(filename)
    return {
        "instrument_version": f"spice#{version}",
        "time_utc": kernel_set_key,
        "instrument": "spice",
        "spice_kernels": spice_kernels,
        "ttj2000ns": kernel_set_key_ttj2000ns,
    }


def ancillary_item(
    ancillary_files: dict,
    version: str,
    kernel_set_key: str,
    kernel_set_key_ttj2000ns: int,
) -> dict:
    """Create the item for the ancillary files used.

    Parameters
    ----------
    ancillary_files : dict
        Ancillary filenames used, keyed by instrument_descriptor.
    version : str
        The data version, e.g. "v001".
    kernel_set_key : str
        The kernel set identifier, used as time_utc.
    kernel_set_key_ttj2000ns : int
        The kernel set key in TTJ2000 ns.

    Returns
    -------
    item : dict
        The ancillary item.
    """
    return {
        "instrument_version": f"ancillary#{version}",
        "time_utc": kernel_set_key,
        "instrument": "ancillary",
        "ancillary_files": ancillary_files,
        "software_versions": SOFTWARE_VERSIONS,
        "ttj2000ns": kernel_set_key_ttj2000ns,
    }


def process_day(instrument: str, day: datetime, version: str, url: str, table):  # noqa: PLR0912
    """Process one day of I-ALiRT SSR data for one instrument.

    Parameters
    ----------
    instrument : str
        The instrument to process, e.g. "mag".
    day : datetime
        The day to process.
    version : str
        The data version, e.g. "v001".
    url : str
        The IMAP data access API URL.
    table : dynamodb.Table
        The SSR DynamoDB table.
    """
    # /tmp persists between warm invocations. Clear it first in case a
    # previous invocation failed or timed out before cleaning up.
    shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)
    DOWNLOAD_DIR.mkdir(parents=True)

    start = time.perf_counter()
    kernel_types = KERNELS if instrument in GEOMETRY_INSTRUMENTS else TIME_KERNELS
    dependencies = get_latest_spice_kernels(url, day, kernel_types)
    spice_files = download_spice_files(dependencies, DOWNLOAD_DIR)
    size_mb = sum(path.stat().st_size for path in spice_files) / 1e6
    logger.info(
        "Downloaded %d SPICE files (%.1f MB) in %.1f s",
        len(spice_files),
        size_mb,
        time.perf_counter() - start,
    )

    kernel_set_key = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")

    # Only these kernels are loaded inside the block. They are unloaded when
    # the block exits, even on an error, so none carry over to the next
    # invocation in a warm Lambda.
    with spiceypy.KernelPool([str(path) for path in spice_files]):
        kernel_set_key_ttj2000ns = int(et_to_ttj2000ns(str_to_et(kernel_set_key)))
        items = [
            spice_item(dependencies, version, kernel_set_key, kernel_set_key_ttj2000ns)
        ]
        if instrument == "spacecraft":
            # The spacecraft has no ancillary files.
            ancillary_files = {}
            items += spacecraft_items(day, version, kernel_set_key)
        else:
            combined = parse_packets(query_filenames(day), DOWNLOAD_DIR)
            check_day_coverage(combined, day)
            combined = trim_packets(combined, day)
            if instrument == "mag":
                result, ancillary_files = process_mag(combined, day, DOWNLOAD_DIR)
            elif instrument == "hit":
                # HIT has no ancillary files.
                result, ancillary_files = process_hit(combined), {}
            elif instrument == "swe":
                result, ancillary_files = process_swe_ssr(combined, day, DOWNLOAD_DIR)
            elif instrument == "codice_lo":
                result, ancillary_files = process_codice_lo(combined, day, DOWNLOAD_DIR)
            elif instrument == "codice_hi":
                result, ancillary_files = process_codice_hi(combined, day, DOWNLOAD_DIR)
            elif instrument == "swapi":
                result, ancillary_files = process_swapi(combined, day, DOWNLOAD_DIR)
            elif instrument == "spacecraft_status":
                # Spacecraft status has no ancillary files.
                result, ancillary_files = process_spacecraft_status(combined), {}
            else:
                raise ValueError(f"Unknown instrument: {instrument}")
            if not (any(result) and all(result)):
                logger.info("No %s products for %s %s", instrument, day, version)
                return
            result = trim_to_day(result, day)
            logger.info("[%s] results populated for [%s]", len(result), instrument)
            items += format_items(result, instrument, version, kernel_set_key)
            if instrument == "mag":
                items += mag_hk_items(result, version, kernel_set_key)
        # Always add the ancillary item so every run records its software versions.
        items.append(
            ancillary_item(
                ancillary_files,
                version,
                kernel_set_key,
                kernel_set_key_ttj2000ns,
            )
        )

    with table.batch_writer(
        overwrite_by_pkeys=["instrument_version", "time_utc"]
    ) as batch:
        for item in items:
            batch.put_item(Item=item)

    logger.info("Inserted %d %s items for %s %s", len(items), instrument, day, version)


def lambda_handler(event, context):
    """Process one day of I-ALiRT SSR data for one instrument.

    Parameters
    ----------
    event : dict
        {"day": "YYYYMMDD", "version": "v001"}
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.
    """
    logger.info("Received event: %s", json.dumps(event))

    instrument = os.environ["INSTRUMENT"]
    url = os.environ["IMAP_DATA_ACCESS_URL"]
    table = boto3.resource("dynamodb").Table(os.environ["SSR_TABLE"])

    day = datetime.strptime(event["day"], "%Y%m%d")
    version = event["version"]

    try:
        # Unreleased L0 files are only returned to queries with an API key.
        ssm_response = boto3.client("ssm").get_parameter(
            Name=os.environ["SSM_API_KEY_PARAMETER"], WithDecryption=True
        )
        imap_data_access.config["API_KEY"] = ssm_response["Parameter"]["Value"]
        process_day(instrument, day, version, url, table)
    except Exception as e:
        logger.error(
            "FAILED %s %s %s: %s: %s",
            instrument,
            event["day"],
            version,
            type(e).__name__,
            e,
        )
        raise
