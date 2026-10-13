"""Weekly Dagster job that fills in L0 data gaps.

The packet downloader Lambda
(``sds_data_manager/lambda_code/SDSCode/pipeline_lambdas/packet_downloader.py``)
downloads L0 data every 6 hours. If a download is missed or incomplete, the
corresponding L0 files are never recreated. This module adds a backstop, as
requested in upstream issue #1617:

- An asset (``l0_data_gap_fill``) that queries WebPODA by spacecraft time
  (S/C time) for a date range and lets ``imap_data_access`` create a new L0
  file, with an incremented minor version, only for data that actually
  changed compared to the latest existing L0 file.
- A sensor (``l0_data_gap_fill_sensor``) that runs the asset once a week for
  the window from one month before one week ago, through one week ago. The
  one-week lag gives the regular 6-hour Lambda a chance to ingest the data
  first, so this job only fills in what was missed.

The gap-filling behavior itself (SCT query, comparison against the latest
production file, and writing a new minor version only when the data changed)
comes from ``imap_data_access.webpoda`` in imap-data-access >= 1.3.0, via the
``query_by_ert=False`` argument of its download functions.
"""

import calendar
import csv
import datetime
import logging
import os
from pathlib import Path

import boto3
import imap_data_access
from dagster import (
    AssetExecutionContext,
    AssetSelection,
    RunRequest,
    SensorEvaluationContext,
    asset,
    define_asset_job,
    sensor,
)
from imap_data_access import webpoda

from sds_data_manager.orchestration import config

logger = logging.getLogger(__name__)

# ENA imagers group by repointing; all other instruments are downloaded by
# day. This matches REPOINTING_INSTRUMENTS in the packet downloader Lambda.
REPOINTING_INSTRUMENTS = {"glows", "hi", "lo", "ultra"}

# The sensor is evaluated at most once per config.DATA_GAP_FILLER_INTERVAL
# (one week), which is what limits this job to a weekly run; Dagster also
# deduplicates runs by run_key, which encodes the window being checked. The
# schedule constants live in config.py alongside the other sensor settings.


def _now() -> datetime.datetime:
    """Return the current time in UTC. Separated out for testing."""
    return datetime.datetime.now(datetime.timezone.utc)


def _subtract_one_month(
    dt: datetime.datetime,
) -> datetime.datetime:
    """Return ``dt`` shifted back one calendar month, clamping the day."""
    year = dt.year
    month = dt.month - 1
    if month == 0:
        year -= 1
        month = 12
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def get_gap_check_window(
    now: datetime.datetime,
) -> tuple[datetime.datetime, datetime.datetime]:
    """Return the (start, end) date range a gap check should cover.

    The window is the month of data ending one week before ``now``, with
    both bounds at midnight UTC: (one week ago minus one month, one week
    ago). This is the window described in upstream issue #1617.

    Parameters
    ----------
    now : datetime.datetime
        The reference time, expected to be timezone-aware.

    Returns
    -------
    tuple[datetime.datetime, datetime.datetime]
        The (start_time, end_time) bounds of the gap check window.
    """
    end = (now - config.DATA_GAP_FILLER_DELAY).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = _subtract_one_month(end)
    return start, end


def setup_environment() -> None:
    """Configure imap_data_access for WebPODA downloads from a Dagster run.

    This mirrors ``setup_environment`` in the packet downloader Lambda: the
    WebPODA token comes from the Secrets Manager secret ``webpoda-token``
    and the IMAP API key from SSM Parameter Store. Dagster runs already
    receive the ``SSM_API_KEY_PARAMETER`` environment variable (see
    ``dagster_env_vars`` in utils/stackbuilder.py), and the Dagster task
    role can read both sources. No secrets are hardcoded, and a missing API
    key only logs a warning, as in the Lambda.
    """
    region = os.environ.get("AWS_REGION") or os.environ.get("REGION")

    secrets_manager = boto3.client("secretsmanager", region_name=region)
    response = secrets_manager.get_secret_value(SecretId="webpoda-token")
    imap_data_access.config["WEBPODA_TOKEN"] = response["SecretString"]

    ssm_parameter_name = os.environ.get(
        "SSM_API_KEY_PARAMETER", "/imap-sdc/batch-jobs/api-key"
    )
    try:
        ssm = boto3.client("ssm", region_name=region)
        ssm_response = ssm.get_parameter(Name=ssm_parameter_name, WithDecryption=True)
        imap_data_access.config["API_KEY"] = ssm_response["Parameter"]["Value"]
    except Exception as e:
        raise RuntimeError("Could not retrieve API key from SSM") from e

    imap_data_access.config["DATA_DIR"] = Path("/tmp")  # noqa: S108


def _load_repoint_data() -> list | None:
    """Download the latest repoint table and return its rows, or None.

    The rows are in the format ``download_repointing_data`` expects: one
    dict per row of the repointing file, as parsed by ``csv.DictReader``
    (the same parsing imap-data-access applied to the file itself before
    version 1.3.0).
    """
    repoint_file = webpoda.get_repoint_file()
    if repoint_file is None:
        logger.warning(
            "No repointing file found; skipping ENA (repointing) instruments "
            "for this gap check."
        )
        return None
    with open(repoint_file) as f:
        return list(csv.DictReader(f))


def fill_data_gaps(start_time: datetime.datetime, end_time: datetime.datetime) -> dict:
    """Query WebPODA by S/C time for a date range and fill in missing L0 data.

    For every instrument, freshly queried data is compared against the
    latest existing L0 file by ``imap_data_access``: a new file with an
    incremented minor version is created and uploaded only if the data
    changed, and nothing is created for unchanged or empty data.

    Parameters
    ----------
    start_time : datetime.datetime
        Start of the date range to check, in spacecraft time.
    end_time : datetime.datetime
        End of the date range to check, in spacecraft time.

    Returns
    -------
    dict
        A summary with the instruments checked by download type.
    """
    logger.info(f"Checking for L0 data gaps from {start_time} to {end_time}")

    in_situ_instruments = sorted(set(webpoda.INSTRUMENT_APIDS) - REPOINTING_INSTRUMENTS)
    for instrument in in_situ_instruments:
        logger.info(f"Checking data gaps for instrument: {instrument}")
        webpoda.download_daily_data(
            instrument=instrument,
            start_time=start_time,
            end_time=end_time,
            upload_to_sdc=True,
            query_by_ert=False,
        )

    repoint_instruments = sorted(set(webpoda.INSTRUMENT_APIDS) & REPOINTING_INSTRUMENTS)
    repoint_data = _load_repoint_data() if repoint_instruments else None
    if repoint_data is not None:
        for instrument in repoint_instruments:
            logger.info(f"Checking data gaps for instrument: {instrument}")
            webpoda.download_repointing_data(
                instrument=instrument,
                start_time=start_time.astimezone(datetime.timezone.utc).replace(
                    tzinfo=None
                ),
                end_time=end_time.astimezone(datetime.timezone.utc).replace(
                    tzinfo=None
                ),
                repoint_data=repoint_data,
                upload_to_sdc=True,
                query_by_ert=False,
            )
    else:
        repoint_instruments = []

    return {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "daily_instruments": in_situ_instruments,
        "repointing_instruments": repoint_instruments,
    }


@asset
def l0_data_gap_fill(context: AssetExecutionContext):
    """Fill in any L0 data missed by the packet downloader Lambda.

    Queries the month of data ending one week ago, by spacecraft time. See
    the module docstring and ``fill_data_gaps``.
    """
    start_time, end_time = get_gap_check_window(_now())
    context.log.info(f"Checking for L0 data gaps from {start_time} to {end_time}")
    setup_environment()
    summary = fill_data_gaps(start_time, end_time)
    context.log.info(f"Data gap check complete: {summary}")


gap_fill_job = define_asset_job(
    name="l0_data_gap_fill_job",
    selection=AssetSelection.assets(l0_data_gap_fill),
)


@sensor(
    name="l0_data_gap_fill_sensor",
    job=gap_fill_job,
    minimum_interval_seconds=int(config.DATA_GAP_FILLER_INTERVAL.total_seconds()),
)
def l0_data_gap_fill_sensor(context: SensorEvaluationContext):
    """Trigger the L0 data gap filler once a week.

    The sensor's minimum interval (``config.DATA_GAP_FILLER_INTERVAL``)
    limits evaluations to once a week, so every evaluation yields one run.
    The run key encodes the end of the window being checked, so the same
    week cannot be checked twice. Note the weekly cadence is anchored to
    whenever the sensor is first turned on, not to a fixed weekday.
    """
    _, end_time = get_gap_check_window(_now())
    run_key = f"l0_data_gap_fill_{end_time.strftime('%Y%m%d')}"
    context.log.info(f"Yielding a run request with ID: {run_key}")
    yield RunRequest(run_key=run_key)


assets = [l0_data_gap_fill]
jobs = [gap_fill_job]
sensors = [l0_data_gap_fill_sensor]
