"""Contains the lambda handler for the 'query' data access API."""

import datetime
import json
import logging

from sqlalchemy import func, select

from ..database import database as db
from ..database import models

from imap_data_access import SPICEFilePath

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
    with db.Session() as session:
        # select the SPICE files table for the query
        query = select(models.SPICEFiles)

        # get a list of all valid search parameters
        valid_parameters = ["start_time", "end_time", "type", "latest"]
        
        # go through each query parameter to set up sqlalchemy query conditions
        for param, value in query_params.items():
            # confirm that the query parameter is valid
            if param not in valid_parameters:
                response = {
                    "statusCode": 400,
                    "body": json.dumps(
                        f"{param} is not a valid query parameter. "
                        + f"Valid query parameters are: {valid_parameters}"
                    ),
                    "headers": {
                        "Content-Type": "application/json",
                        "Access-Control-Allow-Origin": "*",  # Allow CORS
                    },
                }
                logger.debug(
                    f"Received an invalid query parameter [{param}],"
                    " valid options are: {valid_parameters}"
                )
                return response
            
            if param == "start_time":
                query = query.where(models.SPICEFiles.max_date_j2000 >= int(value))
            elif param == "end_time":
                query = query.where(models.SPICEFiles.min_date_j2000 <= int(value))
            elif param == "type":
                query = query.where(models.SPICEFiles.kernel_type == value)
            elif param == "latest" and value.lower()=='true':
                # Make a subquery that gives us (file_root, MAX(version))
                latest_versions_subq = (
                    session.query(
                        models.SPICEFiles.file_root,
                        func.max(models.SPICEFiles.version).label("max_version"),
                    )
                    .group_by(models.SPICEFiles.file_root)
                    .subquery()
                )

                # Join main query to subquery so that we only keep rows
                # with the matching max version for each file_root
                query = query.join(
                    latest_versions_subq,
                    (models.SPICEFiles.file_root == latest_versions_subq.c.file_root)
                    & (models.SPICEFiles.version == latest_versions_subq.c.max_version),
                )

        search_results = session.execute(query).scalars().all()

    search_results = [_convert_spice_metadata_model_to_dict(result) for result in search_results]
    logger.info(
        "Found [%s] Query Search Results: %s", len(search_results), str(search_results)
    )

    # Format the response
    response = {
        "statusCode": 200,
        "body": json.dumps(search_results),  # returns a list of tuples
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",  # Allow CORS
        },
    }

    return response


def _convert_spice_metadata_model_to_dict(file: models.SPICEFiles) -> dict:
    """Convert a sqlalchemy query to SPICEFiles to a dictionary.

    Paramters
    ----------
    file: models.SPICEFiles
        A single row from the SPICEFiles table

    Returns
    -------
    spice_file_dict: dict
        The SPICE file query as a dictionary
    """
    spice_file_dict = {
        "file_name": (
            SPICEFilePath(file.file_name).construct_path().parent.name
            + "/"
            + file.file_name
        ),
        "file_root": file.file_root,
        "kernel_type": file.kernel_type,
        "version": file.version,
        "min_date_j2000": file.min_date_j2000,
        "max_date_j2000": file.max_date_j2000,
        "file_intervals_j2000": file.file_intervals_j2000,
        "min_date_datetime": file.min_date_datetime.strftime("%Y-%m-%d, %H:%M:%S"),
        "max_date_datetime": file.max_date_datetime.strftime("%Y-%m-%d, %H:%M:%S"),
        "file_intervals_datetime": file.file_intervals_datetime,
        "min_date_sclk": file.min_date_sclk,
        "max_date_sclk": file.max_date_sclk,
        "file_intervals_sclk": file.file_intervals_sclk,
        "sclk_kernel": file.sclk_kernel,
        "lsk_kernel": file.lsk_kernel,
        "ingestion_date": file.ingestion_date.strftime("%Y-%m-%d, %H:%M:%S"),
        "timestamp": file.ingestion_date.timestamp(),
    }
    return spice_file_dict
