"""Contains the lambda handler for the 'query' data access API."""

import datetime
import json
import logging

from sqlalchemy import select

from .database import database as db
from .database import models

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
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

    """
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    logger.info("Received event: " + json.dumps(event, indent=2))

    # add session, pick model like in indexer and add query to filter_as
    query_params = event["queryStringParameters"]

    # select the file catalog for the query
    query = select(models.FileCatalog.__table__)
    # get a list of all valid search parameters
    valid_parameters = [
        column.key
        for column in models.FileCatalog.__table__.columns
        if column.key not in ["id"]
    ]
    # Up until this point, valid_parameters are the same as the
    # columns in the FileCatalog table. And looks like we removed
    # the "id" column from the list. But we also need to add
    # 'end_date' to the list of valid_parameters.
    valid_parameters.append("end_date")

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
        # check if we're search for start_date or end date to
        # setup the correct "where" time condition
        if param == "start_date":
            query = query.where(
                models.FileCatalog.start_date
                >= datetime.datetime.strptime(value, "%Y%m%d")
            )
        elif param == "end_date":
            # TODO: Need to discuss as a team how to handle date queries. For now,
            # the date queries will only look at the file start_date.
            query = query.where(
                models.FileCatalog.start_date
                <= datetime.datetime.strptime(value, "%Y%m%d")
            )
        # all non-time string matching parameters
        else:
            query = query.where(getattr(models.FileCatalog, param) == value)

    # We want to order the query returns by the filename
    # This will implicitly sort by: instrument, data level, descriptor, start_date, ...
    # Default for the table is by the ascending id so by insertion order
    query = query.order_by(models.FileCatalog.file_path)

    with db.Session() as session:
        search_results = session.execute(query).all()

    # Convert the search results (list of tuples) to a list of dicts
    search_results = [result._asdict() for result in search_results]

    # Convert datetimes to string values of format 'YYYYMMDD'
    # Also remove values that are not needed by users
    for result in search_results:
        result["start_date"] = result["start_date"].strftime("%Y%m%d")
        d = result["ingestion_date"]
        if d.tzinfo is not None:
            # If the datetime has a timezone, convert it to UTC and remove the timezone
            d = d.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        result["ingestion_date"] = d.strftime("%Y-%m-%d %H:%M:%S")
        del result["id"]

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
