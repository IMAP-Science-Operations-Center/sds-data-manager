"""Test the SDS API manager."""

import pytest
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_lambda as lambda_
from aws_cdk.assertions import Template

from sds_data_manager.constructs.api_gateway_construct import ApiGateway
from sds_data_manager.constructs.data_bucket_construct import DataBucketConstruct
from sds_data_manager.constructs.networking_construct import NetworkingConstruct
from sds_data_manager.constructs.sds_api_manager_construct import SdsApiManager


@pytest.fixture()
def template(stack, env):
    """Return the data bucket stack."""
    data_bucket = DataBucketConstruct(stack, "indexer-data-bucket", env=env)
    networking_construct = NetworkingConstruct(stack, "Networking")
    test_security_group = ec2.SecurityGroup(
        stack, "TestSecurityGroup", vpc=networking_construct.vpc
    )
    apigw = ApiGateway(
        stack,
        construct_id="Api-manager-ApigwTest",
    )
    SdsApiManager(
        stack,
        "api-manager",
        code=lambda_.Code.from_inline("def handler(event, context):\n    pass"),
        env=env,
        api=apigw,
        data_bucket=data_bucket.data_bucket,
        vpc=networking_construct.vpc,
        rds_security_group=test_security_group,
        db_secret_name="test-secrets",  # noqa
        layers=[],
    )

    template = Template.from_stack(stack)
    return template


def test_indexer_role(template):
    """Ensure that the template has appropriate IAM roles."""
    template.resource_count_is("AWS::IAM::Role", 8)
    # Ensure that the template has appropriate lambda count
    template.resource_count_is("AWS::Lambda::Function", 6)
