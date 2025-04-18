"""DPS kernel lambda."""

import json
import logging
import os

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Create dps kernel.

    TODO.

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
    logger.info("Received event: %s", json.dumps(event))

    s3_filepath = event["detail"]["path"]
    filename = os.path.basename(s3_filepath)
    logger.info("Retrieved filename: %s", filename)

    # TODO: Add more logic here
