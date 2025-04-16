"""Test the IalirtArchiveConstruct."""

from pathlib import Path

import pytest
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_s3 as s3
from aws_cdk.assertions import Match, Template

from sds_data_manager.constructs.ialirt_archive_construct import IalirtArchiveConstruct


@pytest.fixture
def template(stack):
    """Create a template with the IalirtArchiveConstruct."""
    ialirt_bucket = s3.Bucket(stack, "MockIalirtBucket")
    algorithm_table = ddb.Table(
        stack,
        "MockAlgorithmTable",
        partition_key=ddb.Attribute(name="apid", type=ddb.AttributeType.NUMBER),
        sort_key=ddb.Attribute(name="met", type=ddb.AttributeType.NUMBER),
    )

    docker_dir = (
        Path(__file__).resolve().parent.parent.parent / "sds_data_manager/lambda_code"
    )

    IalirtArchiveConstruct(
        stack,
        "IalirtArchiveConstruct",
        algorithm_data_table=algorithm_table,
        ialirt_bucket=ialirt_bucket,
        docker_path=str(docker_dir),
    )

    return Template.from_stack(stack)


def test_creates_lambda_and_rule(template):
    """Ensure the Lambda and scheduled EventBridge rule exist."""
    template.resource_count_is("AWS::Lambda::Function", 1)
    template.resource_count_is("AWS::Events::Rule", 1)

    template.has_resource_properties(
        "AWS::Events::Rule", {"ScheduleExpression": "cron(0 0 * * ? *)"}
    )


def test_lambda_has_env_variables(template):
    """Check Lambda has required environment variables."""
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "ialirt-archive",
            "Environment": {
                "Variables": {
                    "ALGORITHM_TABLE": Match.any_value(),
                    "S3_BUCKET": Match.any_value(),
                }
            },
        },
    )
