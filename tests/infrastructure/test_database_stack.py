import pytest
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk.assertions import Template

from sds_data_manager.stacks.database_stack import SdpDatabase
from sds_data_manager.stacks.networking_stack import NetworkingStack


@pytest.fixture(scope="module")
def networking_stack(app, env):
    networking = NetworkingStack(app, "Networking", env=env)
    return networking


@pytest.fixture(scope="module")
def template(app, networking_stack, env):
    rds_size = "SMALL"
    rds_class = "BURSTABLE3"
    rds_storage = 200
    stack = SdpDatabase(
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
    template = Template.from_stack(stack)

    return template


def test_secret_manager_resource_count(template):
    template.resource_count_is("AWS::SecretsManager::Secret", 1)


def test_scecret_target_attachment_resource_count(template):
    template.resource_count_is("AWS::SecretsManager::SecretTargetAttachment", 1)


def test_rds_db_subnet_group_count(template):
    template.resource_count_is("AWS::RDS::DBSubnetGroup", 1)


def test_rds_instance_resource_count(template):
    template.resource_count_is("AWS::RDS::DBInstance", 1)


def test_rds_resource_properties(template):
    template.has_resource_properties(
        "AWS::RDS::DBInstance",
        props={
            "AllocatedStorage": "100",
            "CopyTagsToSnapshot": True,
            "DBInstanceClass": "db.t3.small",
            "DBName": "imapdb",
            "DBSubnetGroupName": {"Ref": "RdsInstanceSubnetGroup9495D83D"},
            "DeletionProtection": False,
            "Engine": "postgres",
            "EngineVersion": "15.3",
            "MaxAllocatedStorage": 200,
            "PubliclyAccessible": True,
            "StorageType": "gp2",
        },
    )
