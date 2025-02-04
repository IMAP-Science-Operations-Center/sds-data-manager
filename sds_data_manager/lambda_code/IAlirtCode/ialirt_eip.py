"""IALiRT Elastic IP Debugging Lambda."""

import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_eip_allocation_id():
    """Retrieve Elastic IP allocation IDs from AWS Secrets Manager.

    Returns
    -------
    eip_id: str
        Elastic IP allocation ID.
    """
    secret_name = os.getenv("EIP_SECRET_NAME")
    secrets = boto3.client("secretsmanager", region_name="us-west-2")

    secret_string = secrets.get_secret_value(SecretId=secret_name)["SecretString"]
    secret_data = json.loads(secret_string)
    eip_allocation_id = secret_data.get("eip_allocation_id")
    logger.info("Retrieved Elastic IP from Secrets Manager: %s", eip_allocation_id)

    return eip_allocation_id


def assign_elastic_ip(instance_id, eip_allocation_id):
    """Assign an available Elastic IP to the specified EC2 instance."""
    ec2 = boto3.client("ec2", region_name="us-west-2")
    response = ec2.describe_addresses(AllocationIds=[eip_allocation_id])
    # Disassociate any existing Elastic IP and associate the new one
    if 'AssociationId' in response["Addresses"][0]:
        association_id = response['Addresses'][0]['AssociationId']
        ec2.disassociate_address(AssociationId=association_id)
    ec2.associate_address(InstanceId=instance_id, AllocationId=eip_allocation_id)


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
    eip_allocation_id = get_eip_allocation_id()

    if eip_allocation_id:
        logger.info("Available Elastic IPs: %s", eip_allocation_id)
    else:
        logger.warning("No available Elastic IPs found.")

    # Assign Elastic IP to the instance.
    assign_elastic_ip(instance_id, eip_allocation_id)
