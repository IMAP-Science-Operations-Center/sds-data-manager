"""IALiRT Elastic IP Assignment Lambda."""

import json
import logging
import os
import time

import boto3

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# AWS Clients
SECRETS = boto3.client("secretsmanager", region_name="us-west-2")
EC2 = boto3.client("ec2", region_name="us-west-2")
ASG = boto3.client("autoscaling", region_name="us-west-2")


def check_existing_eip(instance_id):
    """Check if the instance already has an Elastic IP assigned."""
    try:
        addresses = EC2.describe_addresses(Filters=[{"Name": "instance-id", "Values": [instance_id]}])
        if addresses["Addresses"]:
            existing_eip = addresses["Addresses"][0]["PublicIp"]
            logger.info("Instance %s already has Elastic IP: %s", instance_id, existing_eip)
            return existing_eip
    except Exception as e:
        logger.error("Error checking existing Elastic IP for instance %s: %s", instance_id, str(e))
    return None


def is_eip_available(eip_allocation_id):
    """Check if an Elastic IP is available (not currently associated)."""
    try:
        response = EC2.describe_addresses(AllocationIds=[eip_allocation_id])
        if response["Addresses"] and "InstanceId" in response["Addresses"][0]:
            logger.info("EIP %s is already associated with instance %s.", eip_allocation_id, response["Addresses"][0]["InstanceId"])
            return False  # EIP is assigned
        logger.info("EIP %s is available.", eip_allocation_id)
        return True  # EIP is unassociated
    except Exception as e:
        logger.error("Error checking EIP availability for %s: %s", eip_allocation_id, str(e))
        return False


def get_eip_allocation_ids():
    """Retrieve Elastic IP allocation IDs from AWS Secrets Manager."""
    secret_name = os.getenv("SECRET_NAME")
    if not secret_name:
        logger.error("SECRET_NAME environment variable is not set.")
        return []

    try:
        secret_string = SECRETS.get_secret_value(SecretId=secret_name)["SecretString"]
        secret_data = json.loads(secret_string)
        eip_ids = [value for key, value in secret_data.items() if key.startswith("eip_allocation_id")]
        logger.info("Retrieved %d Elastic IPs from Secrets Manager.", len(eip_ids))
        return eip_ids
    except Exception as e:
        logger.error("Error retrieving Elastic IPs from Secrets Manager: %s", str(e))
        return []


def assign_elastic_ip(instance_id, eip_allocation_ids):
    """Assign an available Elastic IP to the specified EC2 instance."""
    if not eip_allocation_ids:
        logger.error("No Elastic IP allocation IDs provided.")
        return None

    existing_eip = check_existing_eip(instance_id)
    if existing_eip:
        logger.info("Skipping EIP assignment for instance %s as it already has EIP %s", instance_id, existing_eip)
        return existing_eip

    for eip_allocation_id in eip_allocation_ids:
        if is_eip_available(eip_allocation_id):
            try:
                EC2.associate_address(InstanceId=instance_id, AllocationId=eip_allocation_id)
                logger.info("Elastic IP %s assigned to instance %s.", eip_allocation_id, instance_id)
                return eip_allocation_id
            except Exception as e:
                logger.error("Error assigning Elastic IP %s to instance %s: %s", eip_allocation_id, instance_id, str(e))

    logger.error("No available Elastic IPs found to assign.")
    return None  # No available EIPs found


def complete_lifecycle_action(asg_name, lifecycle_hook_name, lifecycle_token, result):
    """Complete the Auto Scaling Lifecycle Hook."""
    try:
        ASG.complete_lifecycle_action(
            AutoScalingGroupName=asg_name,
            LifecycleHookName=lifecycle_hook_name,
            LifecycleActionToken=lifecycle_token,
            LifecycleActionResult=result
        )
        logger.info("Completed lifecycle action with result: %s", result)
    except Exception as e:
        logger.error("Error completing lifecycle action: %s", str(e))


def lambda_handler(event, context):
    """Assign an Elastic IP to a newly launched instance.

    This function is triggered by an EventBridge rule listening for
    Auto Scaling Group instance launch events.
    """
    logger.info("Received event: %s", json.dumps(event, indent=2))

    # Extract event details
    instance_id = event["detail"].get("EC2InstanceId")
    asg_name = event["detail"].get("AutoScalingGroupName")
    lifecycle_hook_name = event["detail"].get("LifecycleHookName")
    lifecycle_token = event["detail"].get("LifecycleActionToken")

    if not instance_id or not asg_name or not lifecycle_hook_name or not lifecycle_token:
        logger.error("Missing required event details. Abandoning lifecycle action.")
        complete_lifecycle_action(asg_name, lifecycle_hook_name, lifecycle_token, "ABANDON")
        return

    logger.info("Processing instance: %s", instance_id)

    # Retrieve Elastic IP allocation IDs
    eip_allocation_ids = get_eip_allocation_ids()
    if not eip_allocation_ids:
        logger.error("No available Elastic IPs found in Secrets Manager. Abandoning lifecycle action.")
        complete_lifecycle_action(asg_name, lifecycle_hook_name, lifecycle_token, "ABANDON")
        return

    success = False
    retries = 10  # Max retries for EIP assignment

    while not success and retries > 0:
        retries -= 1
        try:
            assigned_ip = assign_elastic_ip(instance_id, eip_allocation_ids)

            if assigned_ip:
                logger.info("Successfully assigned Elastic IP %s to instance %s", assigned_ip, instance_id)
                success = True
                break
            else:
                logger.warning("Failed to assign an Elastic IP to instance %s. Retrying...", instance_id)

            time.sleep(2 + (retries % 3))  # Random delay between 2-4 seconds

        except Exception as e:
            logger.error("Error during EIP assignment: %s", str(e))
            if retries == 0:
                logger.error("Max retries reached. Abandoning lifecycle action.")
                complete_lifecycle_action(asg_name, lifecycle_hook_name, lifecycle_token, "ABANDON")
                return


    complete_lifecycle_action(asg_name, lifecycle_hook_name, lifecycle_token, "CONTINUE")
