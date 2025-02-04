"""Test the I-Alirt EIP lambda function."""

import json

import boto3
from moto import mock_secretsmanager

from sds_data_manager.lambda_code.IAlirtCode.ialirt_eip import (
    get_eip_allocation_id,
    lambda_handler,
)


@mock_secretsmanager
def test_lambda_handler():
    """Tests the lambda handler."""
    client = boto3.client("secretsmanager", region_name="us-west-2")

    # Mock secret data
    secret_name = "allocation-credentials"  # noqa
    secret_data = {"eip_allocation_id": "eip-12345678"}
    client.create_secret(Name=secret_name, SecretString=json.dumps(secret_data))

    eip_allocation_ids = get_eip_allocation_id()
    assert eip_allocation_ids == ["eip-12345678"]

    event = {"detail": {"EC2InstanceId": "i-0123456789abcdef0"}}
    lambda_handler(event, {})
