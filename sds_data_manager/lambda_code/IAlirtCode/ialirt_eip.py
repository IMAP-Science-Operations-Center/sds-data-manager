import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_eip_allocation_id():
    secret_name = os.getenv("EIP_SECRET_NAME")
    secrets = boto3.client("secretsmanager", region_name="us-west-2")

    secret_string = secrets.get_secret_value(SecretId=secret_name)["SecretString"]
    secret_data = json.loads(secret_string)
    eip_allocation_id = secret_data.get("eip_allocation_id")
    logger.info("Retrieved Elastic IP from Secrets Manager: %s", eip_allocation_id)

    return eip_allocation_id


def assign_elastic_ip(instance_id, eip_allocation_id):
    ec2 = boto3.client("ec2", region_name="us-west-2")
    eip_description = ec2.describe_addresses(AllocationIds=[eip_allocation_id])
    logger.info("response: %s", eip_description)
    # Describe the instance properties
    ec2_description = ec2.describe_instances(InstanceIds=[instance_id])
    logger.info("response2: %s", ec2_description)
    if (
        ec2_description["Addresses"][0]["PublicIp"]
        == ec2_description["Addresses"][0]["PublicIp"]
    ):
        logger.info("Elastic IP is already associated with this instance.")
        return
    elif "AssociationId" in eip_description["Addresses"][0]:
        # Allocation ID is associated with another instance
        # and needs to be disassociated.
        association_id = eip_description["Addresses"][0]["AssociationId"]
        ec2.disassociate_address(AssociationId=association_id)
        logger.info("Elastic IP disassociated from old instance.")

    # Associate the Elastic IP with instance if not already associated with it.
    ec2.associate_address(InstanceId=instance_id, AllocationId=eip_allocation_id)
    logger.info("Elastic IP associated with instance %s", instance_id)


def complete_lifecycle_action(asg_name, lifecycle_hook_name, lifecycle_token, result):
    ec2_client = boto3.client("autoscaling", region_name="us-west-2")
    ec2_client.complete_lifecycle_action(
        AutoScalingGroupName=asg_name,
        LifecycleHookName=lifecycle_hook_name,
        LifecycleActionToken=lifecycle_token,
        LifecycleActionResult=result,
    )
    logger.info("Completed lifecycle action with result: %s", result)


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

    # Retrieve Elastic IP allocation IDs from Secrets Manager
    eip_allocation_id = get_eip_allocation_id()

    if eip_allocation_id:
        logger.info("Available Elastic IPs: %s", eip_allocation_id)
    else:
        logger.warning("No available Elastic IPs found.")

    details = event["detail"]
    if "EC2InstanceId" in details:
        # Instance launch event.
        instance_id = details.get("EC2InstanceId")
    else:
        # Deployment event.
        instance_id = details["instance-id"]

    # Assign Elastic IP to the instance.
    assign_elastic_ip(instance_id, eip_allocation_id)

    if "EC2InstanceId" in details:
        # Complete the lifecycle action
        lifecycle_token = event["detail"]["LifecycleActionToken"]
        asg_name = event["detail"]["AutoScalingGroupName"]
        lifecycle_hook_name = event["detail"]["LifecycleHookName"]
        complete_lifecycle_action(
            asg_name, lifecycle_hook_name, lifecycle_token, "CONTINUE"
        )
        logger.info("Lifecycle Action Completed")
    else:
        logger.info("Instance launch event completed.")
