"""Define lambda to support the spin table API."""

import datetime
import json
import logging

from sqlalchemy import desc, func, select

from ..database import database as db
from ..database.models import RepointFiles, SmallForcesFile, SpinFiles

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_json_response(status_code, body_str, headers=None):
    """Return a formatted JSON response."""
    if headers is None:
        headers = {"Content-Type": "application/json"}
    return {
        "statusCode": status_code,
        "headers": headers,
        "body": json.dumps(body_str),
    }


def _get_query(table, query, param, value):
    if param == "start_date" and table != RepointFiles:
        # Besides repoint table, others have a start_date field
        # This parameter can be ignore for the repointing table
        # because those files have all start dates in every file.
        parsed_date = datetime.datetime.strptime(value, "%Y%m%d")
        query = query.where(table.start_date >= parsed_date)
    elif param == "end_date":
        parsed_date = datetime.datetime.strptime(value, "%Y%m%d")
        query = query.where(table.end_date <= parsed_date)
    elif param == "file_path":
        query = query.where(table.file_path == value)
    elif param == "latest" and value.lower() == "true":
        # TODO: fix this logic
        # Make a subquery that gives latest spin file
        row_number = (
            func.row_number()
            .over(
                partition_by=(table.start_date, table.end_date),
                order_by=desc(table.version),
            )
            .label("row_num")
        )

        # Use a subquery to select only rows where row_num == 1
        # (latest version)
        subquery = select(
            table.file_path,
            table.start_date,
            table.end_date,
            table.version,
            table.ingestion_date,
            row_number,
        )
        query = select(subquery).where(subquery.c.row_num == 1)
    elif param == "start_ingest_date":
        parsed_date = datetime.datetime.strptime(value, "%Y%m%d")
        query = query.where(table.ingestion_date >= parsed_date)
    elif param == "end_ingest_date":
        parsed_date = datetime.datetime.strptime(value, "%Y%m%d")
        query = query.where(table.ingestion_date <= parsed_date)

    return query


def lambda_handler(event, context):
    """Handle API requests for the non-SPICE data.

    Non-SPICE data such as spin, repoint and small-forces.
    """
    logger.debug(
        "Spin/Repoint/small-forces Query Event: " + json.dumps(event, indent=2)
    )

    # Initialize status_code to 400
    status_code = 400
    raw_path = event.get("rawPath", "")
    if "spin" in raw_path:
        table = SpinFiles
    elif "repoint" in raw_path:
        table = RepointFiles
    elif "small-forces" in raw_path:
        table = SmallForcesFile
    else:
        err_msg = "Invalid path: must contain 'spin', 'repoint', or 'small-forces."
        response = get_json_response(status_code, err_msg)
        logger.debug(err_msg)
        return response

    # add session, pick model like in indexer and add query to filter_as
    query_params = event.get("queryStringParameters", {})
    with db.Session() as session:
        # select the SPICE files table for the query
        query = select(table)

        # get a list of all valid search parameters
        valid_parameters = [
            "file_path",
            "start_date",
            "end_date",
            "latest",
            "start_ingest_date",
            "end_ingest_date",
        ]

        # go through each query parameter to set up sqlalchemy query conditions
        for param, value in query_params.items():
            # confirm that the query parameter is valid
            if param not in valid_parameters:
                err_msg = (
                    f"{param} is not a valid query parameter. "
                    f"Valid query parameters are: {valid_parameters}"
                )
                response = get_json_response(status_code, err_msg)
                logger.debug(err_msg)
                return response
            try:
                query = _get_query(table, query, param, value)
            except ValueError:
                err_msg = f"Invalid value for {param}: {value}"
                response = get_json_response(status_code, err_msg)
                logger.debug(err_msg)
                return response

        # Reset status_code to 200 if we got this far
        status_code = 200
        search_results = session.execute(query).scalars().all()
        # format the search results into a list of dictionaries
        if table == RepointFiles:
            # Repointing files do not have a start_date field
            search_results = [
                {
                    "file_path": result.file_path,
                    "end_date": result.end_date.strftime("%Y-%m-%d, %H:%M:%S"),
                    "version": result.version,
                    "ingestion_date": result.ingestion_date.strftime(
                        "%Y-%m-%d, %H:%M:%S"
                    ),
                }
                for result in search_results
            ]
            return get_json_response(status_code, search_results)

        # Spin or small-forces files have a start_date field
        search_results = [
            {
                "file_path": result.file_path,
                "start_date": result.start_date.strftime("%Y-%m-%d, %H:%M:%S"),
                "end_date": result.end_date.strftime("%Y-%m-%d, %H:%M:%S"),
                "version": result.version,
                "ingestion_date": result.ingestion_date.strftime("%Y-%m-%d, %H:%M:%S"),
            }
            for result in search_results
        ]
        return get_json_response(status_code, search_results)
