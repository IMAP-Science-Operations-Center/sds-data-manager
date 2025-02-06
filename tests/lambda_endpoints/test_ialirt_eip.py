"""Test the I-Alirt EIP lambda function."""

import json

import boto3
from moto import mock_ec2, mock_secretsmanager

from sds_data_manager.lambda_code.IAlirtCode.ialirt_eip import (
    assign_elastic_ip,
    get_eip_allocation_id,
)


@mock_secretsmanager
def test_get_eip_allocation_id(monkeypatch):
    """Tests the lambda handler."""
    client = boto3.client("secretsmanager", region_name="us-west-2")
    monkeypatch.setenv("EIP_SECRET_NAME", "allocation-credentials")

    # Mock secret data
    secret_name = "allocation-credentials"  # noqa
    secret_data = {"eip_allocation_id": "eip-12345678"}
    client.create_secret(Name=secret_name, SecretString=json.dumps(secret_data))

    # Call the function to retrieve the EIP allocation ID
    eip_allocation_id = get_eip_allocation_id()

    # Modify the assertion to expect a string instead of a list
    assert eip_allocation_id == "eip-12345678"  # Expected to return a string


@mock_ec2
def test_assign_elastic_ip(caplog):
    """Test the assign_elastic_ip function."""
    # Mock EC2 client
    ec2_client = boto3.client("ec2", region_name="us-west-2")

    # Create a mock EC2 instance
    instance_response = ec2_client.run_instances(
        ImageId="ami-0abcdef1234567890", InstanceType="t2.micro", MinCount=1, MaxCount=1
    )
    instance_id = instance_response["Instances"][0]["InstanceId"]

    # Allocate a new EIP and get the AllocationId
    eip_response = ec2_client.allocate_address(Domain="vpc")
    eip_allocation_id = eip_response["AllocationId"]

    with caplog.at_level("INFO"):
        # Run the function to assign the EIP
        assign_elastic_ip(instance_id, eip_allocation_id, "deploy")
        assert f"Elastic IP associated with instance {instance_id}" in caplog.text
        assign_elastic_ip(instance_id, eip_allocation_id, "deploy")
        assert "Elastic IP is already associated with this instance." in caplog.text
