"""Test the I-Alirt EIP lambda function."""

import json
import logging

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_or_allocate_eip() -> str:
    """Get or Create EIP Allocation ID.

    Returns
    -------
    allocation_id : str
        Elastic IP Allocation ID.
    """
    tag_name = "I-Alirt EIP"
    ec2 = boto3.client("ec2", region_name="us-west-2")
    eip_description = ec2.describe_addresses(
        Filters=[{"Name": "tag:Name", "Values": [tag_name]}]
    )
    addresses = eip_description.get("Addresses", [])
    if addresses:
        allocation_id = addresses[0]["AllocationId"]
        logger.info("Found existing EIP allocation: %s", allocation_id)
        return allocation_id
    else:
        response = ec2.allocate_address(Domain="vpc")
        allocation_id = response["AllocationId"]
        public_ip = response["PublicIp"]
        logger.info(
            "Allocated new EIP: %s (Allocation ID: %s)", public_ip, allocation_id
        )
        ec2.create_tags(
            Resources=[allocation_id],
            Tags=[{"Key": "Name", "Value": tag_name}],
        )
        return allocation_id


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

    eip_allocation_id = get_or_allocate_eip()
    logger.info("Using Elastic IP allocation ID: %s", eip_allocation_id)

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
        ec2_client = boto3.client("autoscaling", region_name="us-west-2")
        ec2_client.complete_lifecycle_action(
            AutoScalingGroupName=asg_name,
            LifecycleHookName=lifecycle_hook_name,
            LifecycleActionToken=lifecycle_token,
            LifecycleActionResult="CONTINUE",
        )
        logger.info("Completed lifecycle action with result CONTINUE")
    else:
        logger.info("Instance deploy event completed.")
