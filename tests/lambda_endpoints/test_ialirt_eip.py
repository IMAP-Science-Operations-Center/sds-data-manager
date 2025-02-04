import pytest
import boto3
import json
from moto import mock_secretsmanager

from sds_data_manager.lambda_code.IAlirtCode.ialirt_eip import lambda_handler, get_eip_allocation_ids


@mock_secretsmanager
def test_lambda_handler():
    # Mock AWS Secrets Manager
    client = boto3.client("secretsmanager", region_name="us-west-2")

    # Mock secret data
    secret_name = "eip-credentials"
    secret_data = {"eip_allocation_id_1": "eip-12345678", "eip_allocation_id_2": "eip-87654321"}
    client.create_secret(Name=secret_name, SecretString=json.dumps(secret_data))

    eip_allocation_ids = get_eip_allocation_ids()
    assert eip_allocation_ids == ["eip-12345678", "eip-87654321"]

    event = {
        "detail": {
            "EC2InstanceId": "i-0123456789abcdef0"
        }
    }
    lambda_handler(event, {})
