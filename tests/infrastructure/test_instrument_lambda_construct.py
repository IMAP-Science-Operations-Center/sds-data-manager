"""Test the indexer lambda."""

import pytest
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk.assertions import Template

from sds_data_manager.constructs.api_gateway_construct import ApiGateway
from sds_data_manager.constructs.data_bucket_construct import DataBucketConstruct
from sds_data_manager.constructs.database_construct import SdpDatabase
from sds_data_manager.constructs.instrument_lambdas import BatchStarterLambda
from sds_data_manager.constructs.networking_construct import NetworkingConstruct


@pytest.fixture
def template(stack, env, code):
    """Indexer lambda setup."""
    data_bucket = DataBucketConstruct(stack, "indexer-data-bucket", env=env)
    networking_construct = NetworkingConstruct(stack, "Networking")
    test_security_group = ec2.SecurityGroup(
        stack, "TestSecurityGroup", vpc=networking_construct.vpc
    )
    apigw = ApiGateway(
        stack,
        construct_id="Api-manager-ApigwTest",
    )
    rds_size = "SMALL"
    rds_class = "BURSTABLE3"
    rds_storage = 200
    database_construct = SdpDatabase(
        stack,
        "RDS",
        vpc=networking_construct.vpc,
        engine_version=rds.PostgresEngineVersion.VER_15_3,
        instance_size=ec2.InstanceSize[rds_size],
        instance_class=ec2.InstanceClass[rds_class],
        max_allocated_storage=rds_storage,
        username="imap",
        secret_name="sdp-database-creds-rds",  # noqa
        database_name="imapdb",
        code=code,
        layers=[],
    )
    BatchStarterLambda(
        stack,
        construct_id="batch-starter-construct",
        env=env,
        api=apigw,
        data_bucket=data_bucket.data_bucket,
        code=code,
        rds_construct=database_construct,
        rds_security_group=test_security_group,
        vpc=networking_construct.vpc,
        sqs_queues=[],
        layers=[],
    )
    template = Template.from_stack(stack)
    return template


def test_batch_starter_resources(template):
    """Ensure the template has appropriate IAM roles."""
    template.resource_count_is("AWS::IAM::Role", 9)
    # 2 for RDS stack + 1 for BatchStarterLambda + 1 for API Gateway + 1 for
    # bucket notifications
    template.resource_count_is("AWS::Lambda::Function", 5)

    # Ensure that there are 4 cadence event bridge schedules
    template.resource_count_is("AWS::Scheduler::Schedule", 4)

    # Verify that each of the expected cadence Rules exists
    schedules = template.find_resources("AWS::Scheduler::Schedule")
    schedule_names = [props["Properties"]["Name"] for props in schedules.values()]

    expected_schedules = [
        "ProcessingCadenceJob_one_month",
        "ProcessingCadenceJob_three_months",
        "ProcessingCadenceJob_six_months",
        "ProcessingCadenceJob_one_year",
    ]

    for schedule in expected_schedules:
        assert schedule in schedule_names
