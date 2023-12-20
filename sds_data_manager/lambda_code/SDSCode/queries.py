# Standard
import json
import logging
import sys

# Installed

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
    # TODO: Replace result using RDS
    search_result = ""
    logger.info("Query Search Results: " + json.dumps(search_result))

    # Format the response
    response = {
        "statusCode": 200,
        "body": json.dumps(search_result),  # Convert JSON data to a string
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",  # Allow CORS
        },
    }
    return response
