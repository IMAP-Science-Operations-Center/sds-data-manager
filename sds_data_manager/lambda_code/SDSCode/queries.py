# Standard
import json
import logging
import sys

from SDSCode.database import models
from SDSCode.database.database import engine
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

    query = select(models.FileCatalog.__table__)
    for param, value in query_params.items():
        query = query.where(getattr(models.FileCatalog, param) == value)

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
