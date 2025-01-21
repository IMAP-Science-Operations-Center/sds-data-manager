"""Define lambda to support the catalog API."""

import logging

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Entry point to the catalog API lambda.

    Blank function for initial setup.

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

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
    }
