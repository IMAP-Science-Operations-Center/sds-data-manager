"""Test the indexer lambda."""

import pytest
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk.assertions import Template

from sds_data_manager.constructs.data_bucket_construct import DataBucketConstruct
from sds_data_manager.constructs.database_construct import SdpDatabase
from sds_data_manager.constructs.indexer_lambda_construct import IndexerLambda
from sds_data_manager.constructs.monitoring_construct import MonitoringConstruct
from sds_data_manager.constructs.networking_construct import NetworkingConstruct


@pytest.fixture()
def template(stack, env, code):
    """Indexer lambda setup."""
    data_bucket = DataBucketConstruct(stack, "indexer-data-bucket", env=env)
    networking_construct = NetworkingConstruct(stack, "Networking")
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
    monitoring_construct = MonitoringConstruct(
        stack, construct_id="MonitoringConstruct"
    )
    IndexerLambda(
        stack,
        "indexer-lambda",
        code=code,
        db_secret_name="test-secrets",  # noqa
        vpc=networking_construct.vpc,
        vpc_subnets=database_construct.rds_subnet_selection,
        rds_security_group=database_construct.rds_security_group,
        data_bucket=data_bucket.data_bucket,
        sns_topic=monitoring_construct.sns_topic_notifications,
        layers=[],
    )

    template = Template.from_stack(stack)
    return template


def test_indexer_role(template):
    """Ensure the template has appropriate IAM roles."""
    template.resource_count_is("AWS::IAM::Role", 6)
    # 4 for RDS stack + 1 for indexer lambda
    template.resource_count_is("AWS::Lambda::Function", 5)
