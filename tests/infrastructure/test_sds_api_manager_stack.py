import pytest

# Installed
from aws_cdk.assertions import Template

from sds_data_manager.stacks.api_gateway_stack import ApiGateway
from sds_data_manager.stacks.data_bucket_stack import DataBucketStack
from sds_data_manager.stacks.networking_stack import NetworkingStack
from sds_data_manager.stacks.sds_api_manager_stack import SdsApiManager


@pytest.fixture(scope="module")
def data_bucket(app, env):
    stack = DataBucketStack(app, "indexer-data-bucket", env=env)
    return stack


@pytest.fixture(scope="module")
def networking_stack(app, env):
    networking = NetworkingStack(app, "Networking", env=env)
    return networking


@pytest.fixture(scope="module")
def api_gateway(app, env):
    apigw = ApiGateway(
        app,
        construct_id="Api-manager-ApigwTest",
    )
    return apigw


@pytest.fixture(scope="module")
def template(app, networking_stack, data_bucket, api_gateway, env):
    stack = SdsApiManager(
        app,
        "api-manager",
        api=api_gateway,
        env=env,
        data_bucket=data_bucket.data_bucket,
        vpc=networking_stack.vpc,
        rds_security_group=networking_stack.rds_security_group,
        db_secret_name="test-secrets",
    )

    template = Template.from_stack(stack)

    return template


def test_indexer_role(template):
    template.resource_count_is("AWS::IAM::Role", 4)


def test_lambda_count(template):
    template.resource_count_is("AWS::Lambda::Function", 4)
