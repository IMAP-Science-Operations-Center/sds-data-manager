"""Tests I-ALiRT processing."""

import boto3
import pytest
import requests


def get_nlb_dns(stack_name, port, container_name):
    """Retrieve DNS for the NLB from CloudFormation."""
    client = boto3.client("cloudformation")
    response = client.describe_constructs(StackName=stack_name)
    output_key = f"LoadBalancerDNS{container_name}{port}"
    outputs = response["Stacks"][0]["Outputs"]
    for output in outputs:
        if output["OutputKey"] == output_key:
            return output["OutputValue"]
    raise ValueError(f"DNS output not found for port {port} in stack.")


@pytest.mark.xfail(reason="Will fail unless IALiRT stack is deployed.")
def test_nlb_response():
    """Test to ensure the NLB responds with HTTP 200 status."""
    ialirt_ports = {"Primary": [8080, 8081], "Secondary": [80]}
    container_ports = {"Primary": 8080, "Secondary": 80}

    for stack_name, ports in ialirt_ports.items():
        for port in ports:
            nlb_dns = get_nlb_dns(f"IalirtProcessing{stack_name}", port, stack_name)
            print(f"Testing URL: {nlb_dns}")
            # Specify a timeout for the request
            response = requests.get(nlb_dns, timeout=10)  # timeout in seconds
            assert (
                response.status_code == 200
            ), f"NLB did not return HTTP 200 on port {port} for {stack_name}"
            assert (
                response.text == f"Hello World from Port {container_ports[stack_name]}."
            ), f"NLB did not return expected text on port {port} for {stack_name}"
            s3_response = requests.get(
                nlb_dns + "/list", timeout=10
            )  # timeout in seconds
            assert (
                f"test_file{container_ports[stack_name]}.txt" in s3_response.text
            ), f"NLB did not return expected file name on port {port} for {stack_name}"
