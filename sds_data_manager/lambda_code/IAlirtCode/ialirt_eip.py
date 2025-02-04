"""IALiRT Elastic IP Debugging Lambda."""

import json
import logging
import os

import boto3

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_eip_allocation_ids():
    """Retrieve Elastic IP allocation IDs from AWS Secrets Manager.

    Returns
    -------
    list[str]
        A list of Elastic IP allocation IDs.
    """
    secret_name = os.getenv("EIP_SECRET_NAME")
    SECRETS = boto3.client("secretsmanager", region_name="us-west-2")

    secret_string = SECRETS.get_secret_value(SecretId=secret_name)["SecretString"]
    secret_data = json.loads(secret_string)
    eip_ids = [value for key, value in secret_data.items() if key.startswith("eip_allocation_id")]
    logger.info("Retrieved %d Elastic IPs from Secrets Manager: %s", len(eip_ids), eip_ids)

    return eip_ids


def lambda_handler(event, context):
    """Print available Elastic IPs when an instance launches.

    This function is triggered by an EventBridge rule listening for
    Auto Scaling Group instance launch events.

    Parameters
    ----------
    event : dict
        The JSON formatted event data from EventBridge.
    context : LambdaContext
        Provides runtime information for the function.

    """
    logger.info("Received event: %s", json.dumps(event, indent=2))

    instance_id = event["detail"].get("EC2InstanceId")
    logger.info("Instance %s has been launched.", instance_id)

    # Retrieve Elastic IP allocation IDs from Secrets Manager
    eip_allocation_ids = get_eip_allocation_ids()

    if eip_allocation_ids:
        logger.info("Available Elastic IPs: %s", eip_allocation_ids)
    else:
        logger.warning("No available Elastic IPs found.")
