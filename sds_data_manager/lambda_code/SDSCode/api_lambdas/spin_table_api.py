"""Define lambda to support the spin table API."""

import datetime
import json
import logging

from sqlalchemy import desc, func, select

from ..database import database as db
from ..database.models import SpinFiles

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Handle API requests for the spin-data endpoint."""
    logger.debug("Spin Query Event: " + json.dumps(event, indent=2))

    # add session, pick model like in indexer and add query to filter_as
    query_params = event["queryStringParameters"]
    with db.Session() as session:
        # select the SPICE files table for the query
        query = select(SpinFiles)

        # get a list of all valid search parameters
        valid_parameters = [
            "file_path",
            "start_time",
            "end_time",
            "latest",
            "start_ingest_date",
            "end_ingest_date",
        ]

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
                }
                logger.debug(
                    f"Received an invalid query parameter [{param}],"
                    " valid options are: {valid_parameters}"
                )
                return response
            try:
                if param == "start_time":
                    parsed_date = datetime.datetime.strptime(value, "%Y%m%d")
                    query = query.where(SpinFiles.start_date >= parsed_date)
                elif param == "end_time":
                    parsed_date = datetime.datetime.strptime(value, "%Y%m%d")
                    query = query.where(SpinFiles.end_date <= parsed_date)
                elif param == "file_path":
                    query = query.where(SpinFiles.file_path == value)
                elif param == "latest" and value.lower() == "true":
                    # Make a subquery that gives latest spin file
                    row_number = (
                        func.row_number()
                        .over(
                            partition_by=(SpinFiles.start_date, SpinFiles.end_date),
                            order_by=desc(SpinFiles.version),
                        )
                        .label("row_num")
                    )

                    # select row_num == 1 since one indicates latest version
                    query = query.where(row_number == 1).all()
                elif param == "start_ingest_date":
                    parsed_date = datetime.datetime.strptime(value, "%Y%m%d")
                    query = query.where(SpinFiles.ingestion_date >= parsed_date)
                elif param == "end_ingest_date":
                    parsed_date = datetime.datetime.strptime(value, "%Y%m%d")
                    query = query.where(SpinFiles.ingestion_date <= parsed_date)
            except ValueError:
                response = {
                    "statusCode": 400,
                    "body": json.dumps(f"Invalid value for {param}: {value}"),
                }
                logger.debug(f"Invalid value for {param}: {value}")
                return response

        search_results = session.execute(query).scalars().all()
    # format the search results into a list of dictionaries
    search_results = [
        {
            "file_path": result.file_path,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "version": result.version,
            "ingestion_date": result.ingestion_date,
        }
        for result in search_results
    ]
    print(event)
    # TODO: extend this lambda code once we finish creating
    # spin table schema
    return {"statusCode": 200, "body": json.dumps(search_results)}
