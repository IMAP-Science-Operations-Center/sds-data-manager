"""Test the I-Alirt EIP lambda function."""

import boto3
from moto import mock_ec2

from sds_data_manager.lambda_code.IAlirtCode.ialirt_eip import (
    assign_elastic_ip,
    get_or_allocate_eip,
    lambda_handler,
)


@mock_ec2
def test_get_or_allocate_eip():
    """Tests the get_or_allocate_eip function using a mocked EC2 client."""
    ec2 = boto3.client("ec2", region_name="us-west-2")

    allocation_id = get_or_allocate_eip()
    addresses = ec2.describe_addresses().get("Addresses", [])

    # Assert that an address was allocated
    assert addresses[0]["AllocationId"] == allocation_id

    # Verify that the allocated address has the proper tag
    tags = addresses[0].get("Tags", [])
    assert any(tag["Key"] == "Name" and tag["Value"] == "I-Alirt EIP" for tag in tags)


@mock_ec2
def test_assign_elastic_ip(caplog, monkeypatch):
    """Test the assign_elastic_ip function."""
    monkeypatch.setenv("ASG_NAME", "test-ialirt-asg")

    # Mock EC2 client
    ec2_client = boto3.client("ec2", region_name="us-west-2")

    # Create a mock EC2 instance
    instance_response = ec2_client.run_instances(
        ImageId="ami-0abcdef1234567890", InstanceType="t2.micro", MinCount=1, MaxCount=1
    )
    instance_id = instance_response["Instances"][0]["InstanceId"]
    ec2_client.create_tags(
        Resources=[instance_id],
        Tags=[{"Key": "aws:autoscaling:groupName", "Value": "test-ialirt-asg"}],
    )

    # Allocate a new EIP and get the AllocationId
    eip_response = ec2_client.allocate_address(Domain="vpc")
    eip_allocation_id = eip_response["AllocationId"]

    with caplog.at_level("INFO"):
        # Run the function to assign the EIP
        assign_elastic_ip(instance_id, eip_allocation_id, "deploy")
        assert f"Elastic IP associated with instance {instance_id}" in caplog.text
        assign_elastic_ip(instance_id, eip_allocation_id, "deploy")
        assert "Elastic IP is already associated with this instance." in caplog.text


@mock_ec2
def test_assign_elastic_ip_skips_other_asg(caplog, monkeypatch):
    """Instances outside the I-ALiRT ASG must not get the EIP reassigned."""
    monkeypatch.setenv("ASG_NAME", "test-ialirt-asg")

    ec2_client = boto3.client("ec2", region_name="us-west-2")

    instance_response = ec2_client.run_instances(
        ImageId="ami-0abcdef1234567890", InstanceType="t2.micro", MinCount=1, MaxCount=1
    )
    instance_id = instance_response["Instances"][0]["InstanceId"]
    ec2_client.create_tags(
        Resources=[instance_id],
        Tags=[{"Key": "aws:autoscaling:groupName", "Value": "some-other-asg"}],
    )

    eip_response = ec2_client.allocate_address(Domain="vpc")
    eip_allocation_id = eip_response["AllocationId"]

    with caplog.at_level("INFO"):
        assign_elastic_ip(instance_id, eip_allocation_id, "deploy")
        assert "skipping EIP assignment" in caplog.text

    addresses = ec2_client.describe_addresses(AllocationIds=[eip_allocation_id])[
        "Addresses"
    ]
    assert "AssociationId" not in addresses[0]


@mock_ec2
def test_assign_elastic_ip_disassociate(caplog, monkeypatch):
    """Test the Elastic IP disassociation functionality."""
    monkeypatch.setenv("ASG_NAME", "test-ialirt-asg")

    # Mock EC2 client
    ec2_client = boto3.client("ec2", region_name="us-west-2")

    # Create a mock EC2 instance
    instance_response = ec2_client.run_instances(
        ImageId="ami-0abcdef1234567890", InstanceType="t2.micro", MinCount=1, MaxCount=1
    )
    instance_id_1 = instance_response["Instances"][0]["InstanceId"]
    ec2_client.create_tags(
        Resources=[instance_id_1],
        Tags=[{"Key": "aws:autoscaling:groupName", "Value": "test-ialirt-asg"}],
    )

    # Create another mock EC2 instance for disassociation
    instance_response_2 = ec2_client.run_instances(
        ImageId="ami-0abcdef1234567890", InstanceType="t2.micro", MinCount=1, MaxCount=1
    )
    instance_id_2 = instance_response_2["Instances"][0]["InstanceId"]

    # Allocate a new EIP and get the AllocationId
    eip_response = ec2_client.allocate_address(Domain="vpc")
    eip_allocation_id = eip_response["AllocationId"]

    # Associate the EIP with the second instance (simulates another
    # instance, e.g. an unrelated one, holding the I-ALiRT EIP)
    ec2_client.associate_address(
        InstanceId=instance_id_2, AllocationId=eip_allocation_id
    )

    # Run the function to assign the EIP to the first instance
    with caplog.at_level("INFO"):
        assign_elastic_ip(instance_id_1, eip_allocation_id, "deploy")
        assert "Elastic IP disassociated from old instance." in caplog.text


@mock_ec2
def test_lambda_handler_deploy(monkeypatch):
    """Test lambda_handler function."""
    monkeypatch.setattr(
        "sds_data_manager.lambda_code.IAlirtCode.ialirt_eip.assign_elastic_ip",
        lambda instance_id, eip_allocation_id, eventtype: None,
    )

    # Create a minimal event that uses the deploy branch.
    event = {"detail": {"instance-id": "i-0abcdef1234567890"}}

    # Call lambda_handler with a dummy context (None).
    lambda_handler(event, None)
