"""Tests I-ALiRT processing stack."""
import boto3
import requests

from sds_data_manager import (
    IALIRT_PORTS_TO_ALLOW_CONTAINER_1,
)


def get_alb_dns(stack_name, port, container_name):
    """Retrieve DNS for the ALB from CloudFormation."""
    client = boto3.client("cloudformation")
    response = client.describe_stacks(StackName=stack_name)
    output_key = f"LoadBalancerDNS{container_name}{port}"
    for output in response["Stacks"][0]["Outputs"]:
        if output["OutputKey"] == output_key:
            return output["OutputValue"]
    raise ValueError(f"DNS output not found for port {port} in stack.")


def test_alb_response_container():
    """Test to ensure the ALB responds with HTTP 200 status."""
    stack_name = "IalirtProcessing"
    containers = {
        "Container1": IALIRT_PORTS_TO_ALLOW_CONTAINER_1,
    }

    for container_name, ports in containers.items():
        for port in ports:
            alb_dns = get_alb_dns(stack_name, port, container_name)
            print(f"Testing URL: {alb_dns}")
            # Specify a timeout for the request
            response = requests.get(alb_dns, timeout=10)  # timeout in seconds
            assert (
                response.status_code == 200
            ), f"ALB did not return HTTP 200 on port {port} for {container_name}"
