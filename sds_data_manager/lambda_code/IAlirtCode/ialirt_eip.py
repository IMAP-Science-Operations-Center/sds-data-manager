"""Test the I-Alirt EIP lambda function."""

import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_eip_allocation_id() -> str:
    """Get EIP Allocation ID from Secrets Manager.

    Returns
    -------
    eip_allocation_id : str
        Elastic IP Allocation ID.
    """
    secret_name = os.getenv("EIP_SECRET_NAME")
    secrets = boto3.client("secretsmanager", region_name="us-west-2")

    secret_string = secrets.get_secret_value(SecretId=secret_name)["SecretString"]
    secret_data = json.loads(secret_string)
    eip_allocation_id = secret_data.get("eip_allocation_id")
    logger.info("Retrieved Elastic IP from Secrets Manager: %s", eip_allocation_id)

    return eip_allocation_id


def assign_elastic_ip(instance_id: str, eip_allocation_id: str, eventtype: str):
    """Assign EIP to Instance.

    Parameters
    ----------
    instance_id : str
        Instance ID.
    eip_allocation_id : str
        Elastic IP Allocation ID.
    eventtype : str
        Event type (launch or deploy).
    """
    ec2 = boto3.client("ec2", region_name="us-west-2")
    eip_description = ec2.describe_addresses(AllocationIds=[eip_allocation_id])
    ec2_description = ec2.describe_instances(InstanceIds=[instance_id])
    logger.info("eventtype%s", eventtype)
    logger.info("Elastic IP Description: %s", eip_description)
    logger.info("EC2 Description: %s", ec2_description)

    if (
        ec2_description["Reservations"][0]["Instances"][0]["PublicIpAddress"]
        == eip_description["Addresses"][0]["PublicIp"]
    ):
        logger.info("Elastic IP is already associated with this instance.")
        return
    elif "AssociationId" in eip_description["Addresses"][0]:
        association_id = eip_description["Addresses"][0]["AssociationId"]
        ec2.disassociate_address(AssociationId=association_id)
        logger.info("Elastic IP disassociated from old instance.")

    ec2.associate_address(InstanceId=instance_id, AllocationId=eip_allocation_id)
    logger.info("Elastic IP associated with instance %s", instance_id)


def complete_lifecycle_action(
    asg_name: str, lifecycle_hook_name: str, lifecycle_token: str
):
    """Complete the lifecycle transition.

    Parameters
    ----------
    asg_name : str
        AutoScaling Group Name.
    lifecycle_hook_name : str
        Lifecycle hook name.
    lifecycle_token : str
        Lifecycle token.
    """
    ec2_client = boto3.client("autoscaling", region_name="us-west-2")
    ec2_client.complete_lifecycle_action(
        AutoScalingGroupName=asg_name,
        LifecycleHookName=lifecycle_hook_name,
        LifecycleActionToken=lifecycle_token,
        LifecycleActionResult="CONTINUE",
    )
    logger.info("Completed lifecycle action with result CONTINUE")


def lambda_handler(event, context):
    """Assign Elastic IPs when an instance launches.

    Parameters
    ----------
    event : dict
        The JSON formatted event data from EventBridge.
    context : LambdaContext
        Provides runtime information for the function.

    """
    logger.info("Received event: %s", json.dumps(event, indent=2))

    eip_allocation_id = get_eip_allocation_id()

    if eip_allocation_id:
        logger.info("Available Elastic IPs: %s", eip_allocation_id)
    else:
        logger.warning("No available Elastic IPs found.")

    details = event["detail"]
    if "EC2InstanceId" in details:
        # Instance launch event.
        instance_id = details.get("EC2InstanceId")
        assign_elastic_ip(instance_id, eip_allocation_id, "launch")
    else:
        # Deployment event.
        instance_id = details["instance-id"]
        assign_elastic_ip(instance_id, eip_allocation_id, "deploy")

    if "EC2InstanceId" in details:
        lifecycle_token = event["detail"]["LifecycleActionToken"]
        asg_name = event["detail"]["AutoScalingGroupName"]
        lifecycle_hook_name = event["detail"]["LifecycleHookName"]
        complete_lifecycle_action(asg_name, lifecycle_hook_name, lifecycle_token)
        logger.info("Lifecycle Action Completed")
    else:
        logger.info("Instance launch event completed.")
