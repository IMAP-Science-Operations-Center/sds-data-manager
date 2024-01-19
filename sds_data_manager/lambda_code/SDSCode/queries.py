# Standard
import json
import logging
import sys

from SDSCode.database import database as db
from SDSCode.database import models
from sqlalchemy import select
from sqlalchemy.orm import Session

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def lambda_handler(event, context):
    """Handler function for making queries.

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

    # go through each query parameter to set up sqlalchemy query conditions
    for param, value in query_params.items():
        if param == "start_date":
            query = query.where(getattr(models.FileCatalog, param) >= value)
        elif param == "end_date":
            # TODO: Need to discuss as a team how to handle date queries. For now,
            # the date queries will only look at the file start_date.
            query = query.where(models.FileCatalog.start_date <= value)
        else:
            query = query.where(getattr(models.FileCatalog, param) == value)
    engine = db.get_engine()
    with Session(engine) as session:
        search_result = session.execute(query).all()

    logger.info("Query Search Results: " + str(search_result))

    # Format the response
    response = {
        "statusCode": 200,
        "body": json.dumps(str(search_result)),
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",  # Allow CORS
        },
    }
    return response
