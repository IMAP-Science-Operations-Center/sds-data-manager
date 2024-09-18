"""Test the API gateway stack."""

import pytest
from aws_cdk import aws_lambda, aws_sns
from aws_cdk.assertions import Match, Template

from sds_data_manager.constructs.api_gateway_construct import ApiGateway


@pytest.fixture()
def template(stack, code):
    """Return a template for the API gateway stack."""
    test_func = aws_lambda.Function(
        stack,
        "test-function",
        code=code,
        handler="handler",
        runtime=aws_lambda.Runtime.PYTHON_3_9,
    )
    test_sns_topic = aws_sns.Topic(stack, "test-sns-topic")
    apigw = ApiGateway(
        stack,
        construct_id="ApigwTest",
    )
    apigw.deliver_to_sns(sns_topic=test_sns_topic)
    apigw.add_route("test-route", "GET", test_func)
    template = Template.from_stack(stack)
    return template


def test_apigw_routes(template):
    """Ensure that the template has the appropriate routes."""
    template.resource_count_is("AWS::ApiGateway::RestApi", 1)
    # One path resource
    template.resource_count_is("AWS::ApiGateway::Resource", 1)
    template.has_resource_properties(
        "AWS::ApiGateway::Resource",
        props={"PathPart": "test-route"},
    )

    # One GET method on that resource
    template.resource_count_is("AWS::ApiGateway::Method", 1)
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        props={"HttpMethod": "GET"},
    )


def test_cloudwatch_alarm(template):
    """Ensure that the template has a CloudWatch alarm configured."""
    template.resource_count_is("AWS::CloudWatch::Alarm", 1)
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        props={
            "ComparisonOperator": "GreaterThanThreshold",
            "EvaluationPeriods": 60,
            "ActionsEnabled": True,
            "AlarmActions": [
                {"Fn::ImportValue": None, "Ref": Match.string_like_regexp("sns")}
            ],
            "DatapointsToAlarm": 1,
            "Dimensions": [{"Name": "ApiName", "Value": Match.any_value()}],
            "MetricName": "Latency",
            "Namespace": "AWS/ApiGateway",
            "Period": 60,
            "Statistic": "Maximum",
            "Threshold": 10000,
            "TreatMissingData": "notBreaching",
        },
    )
