"""Tests I-ALiRT processing."""

import boto3
import pytest
import requests


def get_nlb_dns(stack_name, port, container_name):
    """Retrieve DNS for the NLB from CloudFormation."""
    client = boto3.client("cloudformation")
    response = client.describe_stacks(StackName=stack_name)
    output_key = f"LoadBalancerDNS{container_name}{port}"
    outputs = response["Stacks"][0]["Outputs"]
    for output in outputs:
        if output_key in output["OutputKey"]:
            return output["OutputValue"]
    raise ValueError(f"DNS output not found for port {port} in stack.")


@pytest.mark.xfail(reason="Will fail unless IALiRT stack is deployed.")
def test_nlb_response():
    """Test to ensure the NLB responds with HTTP 200 status."""
    ialirt_ports = {"Primary": [1235, 1234], "Secondary": [1236]}

    for container_name, ports in ialirt_ports.items():
        for port in ports:
            nlb_dns = get_nlb_dns("IalirtStack", port, container_name)
            # Specify a timeout for the request
            response = requests.get(nlb_dns, timeout=10)  # timeout in seconds
            assert (
                response.status_code == 200
            ), f"NLB did not return HTTP 200 on port {port} for {container_name}"
            assert (
                response.text == f"Hello from Port {port}!"
            ), f"NLB did not return expected text on port {port} for {container_name}"
            s3_response = requests.get(
                nlb_dns + "/list", timeout=10
            )  # timeout in seconds
            assert f"test_file{port}.txt" in s3_response.text, (
                f"NLB did not return expected file name on port {port} "
                f"for {container_name}"
            )
