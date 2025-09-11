"""Test the IalirtCoverageConstruct."""

from pathlib import Path

import pytest
from aws_cdk import App, Stack
from aws_cdk import aws_s3 as s3
from aws_cdk.assertions import Match, Template

from sds_data_manager.constructs.ialirt_coverage_construct import (
    IalirtCoverageConstruct,
)


@pytest.fixture
def template():
    """Create a template with the IalirtCoverageConstruct."""
    app = App(context={"account_name": "test", "test": {}})
    stack = Stack(app, "TestStack")

    ialirt_bucket = s3.Bucket(stack, "MockIalirtBucket")

    docker_dir = (
        Path(__file__).resolve().parent.parent.parent / "sds_data_manager/lambda_code"
    )

    IalirtCoverageConstruct(
        stack,
        "IalirtCoverageConstruct",
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
            "FunctionName": "ialirt-coverage",
            "Environment": {
                "Variables": {
                    "S3_BUCKET": Match.any_value(),
                }
            },
        },
    )
