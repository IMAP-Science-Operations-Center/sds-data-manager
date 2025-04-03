"""Test the Dependency finder api endpoint and lambda function."""

import pytest
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_lambda as lambda_
from aws_cdk.assertions import Template

from sds_data_manager.constructs.api_gateway_construct import ApiGateway
from sds_data_manager.constructs.dependency_finder_construct import DependencyFinder
from sds_data_manager.constructs.networking_construct import NetworkingConstruct


@pytest.fixture()
def template(stack, env):
    """Return the dependency finder stack."""
    apigw = ApiGateway(
        stack,
        construct_id="Api-manager-ApigwTest",
    )
    networking_construct = NetworkingConstruct(stack, "Networking")
    test_security_group = ec2.SecurityGroup(
        stack, "TestSecurityGroup", vpc=networking_construct.vpc
    )
    DependencyFinder(
        scope=stack,
        construct_id="DependencyFinder",
        code=lambda_.Code.from_inline("def handler(event, context):\n    pass"),
        layers=[],
        env=env,
        vpc=networking_construct.vpc,
        rds_security_group=test_security_group,
        db_secret_name="test-secrets",  # noqa
        api=apigw,
    )

    template = Template.from_stack(stack)
    return template


def test_dependency_finder_resources(template):
    """Ensure that the template has expected resources."""
    # Ensure that the template has the appropriate resources
    template.resource_count_is("AWS::Lambda::Function", 1)
    template.resource_count_is("AWS::ApiGateway::RestApi", 1)

    template.has_resource_properties(
        "AWS::ApiGateway::Resource", props={"PathPart": "dependency"}
    )
