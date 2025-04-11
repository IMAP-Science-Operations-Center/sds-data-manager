"""Contains the lambda handler for the 'query' data access API."""

import json
import logging
from pathlib import Path

from . import spice_query_api
from .metakernel import MetaKernel

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    logger.info("Received event: " + json.dumps(event, indent=2))

    # add session, pick model like in indexer and add query to filter_as
    query_params = event["queryStringParameters"]
    start_time = query_params["start_time"]
    end_time = query_params["end_time"]
    spice_directory = Path(query_params["spice_path"])
    list_files = query_params["list_files"]
    metakernel = _metakernel_builder(start_time, end_time)

    if list_files.lower() == 'true':
        output = json.dumps(metakernel.return_spice_files_in_order_detailed())
    else:
        output = metakernel.return_tm_file(base_path=spice_directory)

    # Format the response
    response = {
        "statusCode": 200,
        "body": output,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",  # Allow CORS
        },
    }

    return response


def _metakernel_builder(start_time: int, end_time: int) -> MetaKernel:
    """Create a MetaKernel class and inserts files into it."""
    # Create the Metakernel class
    metakernel = MetaKernel(
        start_time,
        end_time,
        allowed_spice_types=[
            "leapseconds",
            "planetary_constants",
            "frames",
            "spacecraft_clock",
            "planetary_ephemeris",
            "spacecraft_ephemeris",
            "spacecraft_attitude",
        ],
    )

    static_files_load_order = [
        "leapseconds",
        "planetary_constants",
        "frames",
        "spacecraft_clock",
        "planetary_ephemeris",
    ]

    for type in static_files_load_order:
        static_spice_file = spice_query_api.lambda_handler(
            {"queryStringParameters": {"type": type, "latest": "True"}}, None
        )
        metakernel.load_spice(
            json.loads(static_spice_file["body"]),
            type,
            "file_intervals_j2000",
            priority_field="timestamp",
        )

    for ephem_type in [
        "ephemeris_reconstructed",
        "ephemeris_nominal",
        "ephemeris_predicted",
        "ephemeris_90days",
        "ephemeris_long",
        "ephemeris_launch",
    ]:
        if len(metakernel.spice_gaps["spacecraft_ephemeris"]) > 0:
            ephem_files = spice_query_api.lambda_handler(
                {
                    "queryStringParameters": {
                        "start_time": start_time,
                        "end_time": end_time,
                        "type": ephem_type,
                        "latest": "True",
                    }
                },
                None,
            )
            metakernel.load_spice(
                json.loads(ephem_files["body"]),
                "spacecraft_ephemeris",
                "file_intervals",
                priority_field="timestamp",
            )

    for attitude_type in ["attitude_history", "attitude_predict"]:
        if len(metakernel.spice_gaps["spacecraft_attitude"]) > 0:
            attitude_files = spice_query_api.lambda_handler(
                {
                    "queryStringParameters": {
                        "start_time": start_time,
                        "end_time": end_time,
                        "type": attitude_type,
                        "latest": "True",
                    }
                },
                None,
            )
            metakernel.load_spice(
                json.loads(attitude_files["body"]),
                "spacecraft_attitude",
                "file_intervals_j2000",
                priority_field="timestamp",
            )

    return metakernel
