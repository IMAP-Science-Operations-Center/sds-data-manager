"""Tests I-ALiRT processing stack."""

import boto3
import pytest
import requests


def get_alb_dns(stack_name, port, container_name):
    """Retrieve DNS for the ALB from CloudFormation."""
    client = boto3.client("cloudformation")
    response = client.describe_stacks(StackName=stack_name)
    output_key = f"LoadBalancerDNS{container_name}{port}"
    outputs = response["Stacks"][0]["Outputs"]
    for output in outputs:
        if output["OutputKey"] == output_key:
            return output["OutputValue"]
    raise ValueError(f"DNS output not found for port {port} in stack.")


@pytest.mark.xfail(reason="Will fail unless IALiRT stack is deployed.")
def test_alb_response():
    """Test to ensure the ALB responds with HTTP 200 status."""
    stacks = {
        "Primary": [8080, 8081],
        "Secondary": [80],
    }

    for stack_name, ports in stacks.items():
        for port in ports:
            alb_dns = get_alb_dns(f"IalirtProcessing{stack_name}", port, stack_name)
            print(f"Testing URL: {alb_dns}")
            # Specify a timeout for the request
            response = requests.get(alb_dns, timeout=10)  # timeout in seconds
            assert (
                response.status_code == 200
            ), f"ALB did not return HTTP 200 on port {port} for {stack_name}"
