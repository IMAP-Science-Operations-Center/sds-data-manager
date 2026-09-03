"""Contains the lambda handler for the 'query' data access API."""

import datetime
import json
import logging
from pathlib import Path

import spiceypy

from ..spice_utilities import (
    furnish_best_spice_file,
    metakernel_builder,
)

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _convert_input_times_to_j2000(start_date_str, end_date_str):
    """Convert input to seconds since J2000.

    Either input may be None, in which case it will be returned as None for that value.
    """

    def _convert_single(date_str):
        if date_str is None:
            return None

        try:
            # Convert to datetime objects
            date_datetime = datetime.datetime.strptime(date_str, "%Y%m%d")
        except (TypeError, ValueError):
            # Not a date string, assume a J2000 seconds value
            return float(date_str)

        # Use SPICE to convert to J2000

        # First, check if LSK is loaded in yet
        count = spiceypy.ktotal("TEXT")
        lsk_loaded = False
        for i in range(count):
            filename, _, _, _ = spiceypy.kdata(i, "TEXT", 100, 100, 100)

            if ".tls" in filename:
                logger.info("Leapsecond kernel is furnished.")
                lsk_loaded = True
                break

        # If it is not loaded, attempt to load it
        if not lsk_loaded:
            logger.info(
                "Attempting to load leapseconds kernel needed for time conversion."
            )
            furnish_best_spice_file("leapseconds")

        return spiceypy.datetime2et(date_datetime)

    return _convert_single(start_date_str), _convert_single(end_date_str)


def lambda_handler(event, context):
    """Entry point to the SPICE query API lambda.

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
    logger.info("Metakernel event: " + json.dumps(event, indent=2))

    # Gather the query parameters, ensure it is not None and instead
    # default to empty dict to avoid error
    query_params = event.get("queryStringParameters") or {}
    start_time_str = query_params.get("start_time")
    end_time_str = query_params.get("end_time")
    # Allow SPICE query to have dates omitted
    query_start_time, query_end_time = _convert_input_times_to_j2000(
        start_time_str, end_time_str
    )
    spice_directory = Path(query_params.get("spice_path", ""))
    list_files = query_params.get("list_files", "false")
    require_coverage = query_params.get("require_coverage", "false")
    file_types = query_params.get("file_types", None)
    if file_types:
        file_types = {type.strip().upper() for type in file_types.split(",")}

    # Build a metakernel
    metakernel = metakernel_builder(
        query_start_time,
        query_end_time,
        file_types=file_types,
    )

    if (require_coverage.lower() == "true") and metakernel.contains_gaps():
        return {
            "statusCode": 422,  # Unprocessable Content
            "body": json.dumps(metakernel.spice_gaps),
        }

    if list_files.lower() == "true":
        metakernel_files = metakernel.return_spice_files_in_order(detailed=False)
        if not metakernel_files:
            return {
                "statusCode": 404,  # Not Found
                "body": "No files found.",
            }
        output = json.dumps([Path(f).name for f in metakernel_files])
    else:
        output = metakernel.return_tm_file(base_path=spice_directory)

    # Format the response
    response = {
        "statusCode": 200,
        "body": output,
    }

    return response
