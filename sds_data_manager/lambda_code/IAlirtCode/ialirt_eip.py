"""IALiRT Elastic IP Assignment Lambda."""

import json
import logging
import os

import boto3

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create a batch client
SECRETS = boto3.client("secretsmanager", region_name="us-west-2")
EC2 = boto3.client("ec2", region_name="us-west-2")
ASG = boto3.client("autoscaling", region_name="us-west-2")


def check_existing_eip(instance_id):
    """Check if the instance already has an Elastic IP assigned.

    Parameters
    ----------
    instance_id : str
        The EC2 instance ID.

    Returns
    -------
    str or None
        The existing Elastic IP allocation ID, or None if not found.
    """
    try:
        addresses = EC2.describe_addresses(Filters=[{"Name": "instance-id", "Values": [instance_id]}])
        if addresses["Addresses"]:
            existing_eip = addresses["Addresses"][0]["PublicIp"]
            logger.info("Instance %s already has Elastic IP: %s", instance_id, existing_eip)
            return existing_eip
        return None
    except Exception as e:
        logger.error("Error checking existing Elastic IP for instance %s: %s", instance_id, str(e))
        return None


def is_eip_available(eip_allocation_id):
    """Check if an Elastic IP is available (not currently associated with any instance).

    Parameters
    ----------
    eip_allocation_id : str
        The Elastic IP allocation ID.

    Returns
    -------
    bool
        True if the EIP is available, False if it is already assigned.
    """
    try:
        response = EC2.describe_addresses(AllocationIds=[eip_allocation_id])
        if response["Addresses"] and "InstanceId" in response["Addresses"][0]:
            logger.info("EIP %s is already associated with instance %s.", eip_allocation_id, response["Addresses"][0]["InstanceId"])
            return False  # EIP is assigned, not available
        logger.info("EIP %s is available.", eip_allocation_id)
        return True  # EIP is unassociated and available
    except Exception as e:
        logger.error("Error checking EIP availability for %s: %s", eip_allocation_id, str(e))
        return False


def get_eip_allocation_ids():
    """Retrieve Elastic IP allocation IDs from AWS Secrets Manager.

    Returns
    -------
    list[str]
        A list of Elastic IP allocation IDs.
    """
    secret_arn = os.environ.get("EIP_SECRET_ARN")
    if not secret_arn:
        logger.error("EIP_SECRET_ARN environment variable is not set.")
        return []

    try:
        secret_name = os.getenv("SECRET_NAME")
        secret_string = SECRETS.get_secret_value(SecretId=secret_name)["SecretString"]
        secret_data = json.loads(secret_string)
        eip_ids = [value for key, value in secret_data.items() if key.startswith("eip_allocation_id")]
        logger.info("Retrieved %d Elastic IPs from Secrets Manager.", len(eip_ids))
        return eip_ids

    except Exception as e:
        logger.error("Error retrieving Elastic IPs from Secrets Manager: %s", str(e))
        return []


def assign_elastic_ip(instance_id, eip_allocation_ids):
    """Assign an Elastic IP to the specified EC2 instance.

    Parameters
    ----------
    instance_id : str
        The EC2 instance ID.
    eip_allocation_ids : list[str]
        A list of available Elastic IP allocation IDs.

    Returns
    -------
    str or None
        The assigned Elastic IP allocation ID, or None if assignment failed.
    """
    existing_eip = check_existing_eip(instance_id)
    if existing_eip:
        logger.info("Skipping EIP assignment for instance %s as it already has EIP %s", instance_id, existing_eip)
        return existing_eip

    if not eip_allocation_ids:
        logger.error("No Elastic IP allocation IDs provided.")
        return None

    for eip_allocation_id in eip_allocation_ids:
        if is_eip_available(eip_allocation_id):  # ✅ Only assign if truly available
            try:
                EC2.associate_address(InstanceId=instance_id, AllocationId=eip_allocation_id)
                logger.info("Elastic IP %s assigned to instance %s.", eip_allocation_id, instance_id)
                return eip_allocation_id
            except Exception as e:
                logger.error("Error assigning Elastic IP to instance %s: %s", instance_id, str(e))
                return None

    logger.error("No available Elastic IPs found to assign.")
    return None  # No available EIPs found


def complete_lifecycle_action(asg_name, lifecycle_hook_name, lifecycle_token, result):
    """Complete the Auto Scaling Lifecycle Hook.

    Parameters
    ----------
    asg_name : str
        The name of the Auto Scaling Group.
    lifecycle_hook_name : str
        The name of the lifecycle hook.
    lifecycle_token : str
        The lifecycle action token.
    result : str
        The result of the lifecycle action ('CONTINUE' or 'ABANDON').

    """
    try:
        response = ASG.complete_lifecycle_action(
            AutoScalingGroupName=asg_name,
            LifecycleHookName=lifecycle_hook_name,
            LifecycleActionToken=lifecycle_token,
            LifecycleActionResult=result
        )
        logger.info("Completed lifecycle action: %s", result)
    except Exception as e:
        logger.error("Error completing lifecycle action: %s", str(e))



def lambda_handler(event, context):
    """Assign an Elastic IP to a newly launched instance.

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

    instance_id = event["detail"]["EC2InstanceId"]
    if not instance_id:
        logger.error("No EC2 Instance ID found in event.")
        return

    logger.info("Instance ID: %s", instance_id)

    # Retrieve Elastic IP allocation IDs from Secrets Manager
    eip_allocation_ids = get_eip_allocation_ids()
    if not eip_allocation_ids:
        logger.error("No available Elastic IPs found in Secrets Manager.")
        return

    # Assign the first available Elastic IP to the instance
    assigned_ip = assign_elastic_ip(instance_id, eip_allocation_ids)

    if assigned_ip:
        logger.info("Successfully assigned Elastic IP %s to instance %s", assigned_ip, instance_id)
    else:
        logger.error("Failed to assign an Elastic IP to instance %s", instance_id)
