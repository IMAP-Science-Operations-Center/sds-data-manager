import pytest
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk.assertions import Match, Template

from sds_data_manager.stacks.create_schema_stack import CreateSchema
from sds_data_manager.stacks.database_stack import SdpDatabase
from sds_data_manager.stacks.networking_stack import NetworkingStack


@pytest.fixture(scope="module")
def networking_stack(app, env):
    networking = NetworkingStack(app, "Networking", env=env)
    return networking


@pytest.fixture(scope="module")
def database_stack(app, networking_stack, env):
    rds_size = "SMALL"
    rds_class = "BURSTABLE3"
    rds_storage = 200
    database_stack = SdpDatabase(
        app,
        "RDS",
        description="IMAP SDP database",
        env=env,
        vpc=networking_stack.vpc,
        rds_security_group=networking_stack.rds_security_group,
        engine_version=rds.PostgresEngineVersion.VER_15_3,
        instance_size=ec2.InstanceSize[rds_size],
        instance_class=ec2.InstanceClass[rds_class],
        max_allocated_storage=rds_storage,
        username="imap",
        secret_name="sdp-database-creds-rds",
        database_name="imapdb",
    )
    return database_stack


@pytest.fixture(scope="module")
def template(app, networking_stack, database_stack, env):
    # create dynamoDB stack
    stack = CreateSchema(
        app,
        construct_id="CreateSchema",
        env=env,
        db_secret_name="0123456789",
        vpc=networking_stack.vpc,
        vpc_subnets=database_stack.rds_subnet_selection,
        rds_security_group=networking_stack.rds_security_group,
    )
    template = Template.from_stack(stack)

    return template


def test_lambda_function_resource_count(template):
    # there is a lambda for creating the schema and another lambda
    # that gets created for the "Provider Framework Event".
    template.resource_count_is("AWS::Lambda::Function", 2)


def test_iam_policy_resource_count(template):
    template.resource_count_is("AWS::IAM::Policy", 2)


def test_iam_role_resource_count(template):
    template.resource_count_is("AWS::IAM::Role", 2)


def test_custom_resource_count(template):
    template.resource_count_is("AWS::CloudFormation::CustomResource", 1)


def test_create_schema_lambda_function_resource_properties(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "FunctionName": "create-schema",
            "Runtime": "python3.9",
            "Handler": "SDSCode.create_schema.lambda_handler",
            "MemorySize": 1000,
            "Timeout": 10,
            "Role": {
                "Fn::GetAtt": [
                    Match.string_like_regexp("CreateMetadataSchemaServiceRole*"),
                    "Arn",
                ]
            },
        },
    )
