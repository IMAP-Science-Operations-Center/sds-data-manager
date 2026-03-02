"""I-ALiRT Packets API."""

import json
import logging
import os
from datetime import datetime

import boto3
import botocore

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):  # noqa: PLR0911, PLR0912, PLR0915
    """Entry point to the query API lambda.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    Notes
    -----
    Based on filename iois_1_packets_YYYY_DOY_HH_MM_SS.

    Two mutually exclusive query modes are supported:

    UTC range mode — both use strict ISO 8601 format YYYY-MM-DDTHH:MM:SS.
    Queries by day (year + DOY) and post-filters by exact time range.
    time_utc_start is required; time_utc_end is optional.

    Individual params mode — partial time specification
    (year, doy[, hh[, mm[, ss]]]). Builds an S3 prefix directly.
    At minimum, year and doy must be provided.

    Example
    -------
    UTC range mode:
        ?time_utc_start=2025-10-29T18:55:02
        ?time_utc_start=2025-10-29T18:55:02&time_utc_end=2025-10-29T19:05:00

    Individual params mode:
        ?year=2025&doy=148&hh=16&mm=24
    """
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    logger.info("Received event: " + json.dumps(event, indent=2))

    query_params = event.get("queryStringParameters") or {}
    time_utc_start = query_params.get("time_utc_start")
    time_utc_end = query_params.get("time_utc_end")
    year = query_params.get("year")
    doy = query_params.get("doy")
    hh = query_params.get("hh")
    mm = query_params.get("mm")
    ss = query_params.get("ss")

    utc_params = {time_utc_start, time_utc_end} - {None}
    individual_params = {year, doy, hh, mm, ss} - {None}

    if utc_params and individual_params:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "error": "Cannot mix UTC range parameters (time_utc_start, "
                    "time_utc_end) with individual parameters (year, doy, hh, mm, ss)."
                }
            ),
        }

    end_dt = None

    if utc_params:
        # --- UTC range mode ---
        if not time_utc_start:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "'time_utc_start' is required."}),
            }

        try:
            start_dt = datetime.fromisoformat(time_utc_start)
        except ValueError as e:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": str(e)}),
            }

        if time_utc_end:
            try:
                end_dt = datetime.fromisoformat(time_utc_end)
            except ValueError as e:
                return {
                    "statusCode": 400,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"error": str(e)}),
                }

            if end_dt <= start_dt:
                return {
                    "statusCode": 400,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps(
                        {"error": "'time_utc_end' must be after 'time_utc_start'."}
                    ),
                }

        # Use year + DOY as the S3 prefix (day granularity) so the full
        # requested range is covered, then post-filter by exact time.
        year = str(start_dt.year)
        doy = str(start_dt.timetuple().tm_yday)
        prefix = f"packets/iois_1_packets_{year}_{doy}"

    else:
        if not year or not doy:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(
                    {"error": "At minimum, 'year' and 'doy' must be provided."}
                ),
            }

        try:
            datetime.strptime(f"{year}{doy}", "%Y%j")
        except ValueError:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(
                    {"error": "Invalid year or day format. Use YYYY and DOY."}
                ),
            }

        parts = [year, doy]
        if hh:
            # Pad values if necessary.
            parts.append(hh.zfill(2))
        if mm:
            # Pad values if necessary.
            parts.append(mm.zfill(2))
        if ss:
            # Pad values if necessary.
            parts.append(ss.zfill(2))

        prefix = "packets/iois_1_packets_" + "_".join(parts)
        start_dt = None

    bucket = os.getenv("S3_BUCKET")
    region = os.getenv("REGION")

    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    files = []

    for obj in response.get("Contents", []):
        filename = obj["Key"].split("/")[-1]
        if start_dt is not None:
            _, _, _, year, doy, hh, mm, ss = filename.split("_")
            file_dt = datetime.strptime(f"{year}{doy}{hh}{mm}{ss}", "%Y%j%H%M%S")
            if file_dt < start_dt:
                continue
            if end_dt and file_dt > end_dt:
                continue
        files.append(filename)

    response = {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"files": files}),
    }

    return response
