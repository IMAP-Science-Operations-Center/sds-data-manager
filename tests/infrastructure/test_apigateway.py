from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk import Duration, Stack, aws_lambda, aws_sns
from aws_cdk.assertions import Match, Template

from sds_data_manager.stacks.api_gateway_stack import ApiGateway, APILambda
from sds_data_manager.stacks.networking_stack import NetworkingStack


@pytest.fixture(scope="module")
def template(app):
    stack = Stack(app, "test-stack")
    test_func = aws_lambda.Function(
        stack,
        "test-function",
        code=aws_lambda.Code.from_inline("def handler(event, context):\n    pass"),
        handler="handler",
        runtime=aws_lambda.Runtime.PYTHON_3_9,
    )
    test_sns_topic = aws_sns.Topic(stack, "test-sns-topic")
    apigw = ApiGateway(
        app,
        construct_id="ApigwTest",
    )
    apigw.deliver_to_sns(sns_topic=test_sns_topic)
    apigw.add_route("test-route", "GET", test_func)
    template = Template.from_stack(apigw)
    return template


@pytest.fixture()
def lambda_template():
    app = cdk.App()
    lambda_code_directory = (
        Path(__file__).parent.parent.parent / "sds_data_manager/lambda_code/SDSCode"
    )
    spin_table_code = lambda_code_directory / "spin_table_api.py"
    vpc = NetworkingStack(app, "networking-stack")
    test_func = APILambda(
        app,
        "SpinLambda",
        lambda_name="test-lambda",
        code_path=spin_table_code,
        lambda_handler="lambda_handler",
        timeout=Duration.seconds(60),
        rds_security_group=vpc.rds_security_group,
        db_secret_name="test-creds",
        vpc=vpc.vpc,
    )

    template = Template.from_stack(test_func)
    return template


def test_apigw_routes(template):
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
    template.resource_count_is("AWS::CloudWatch::Alarm", 1)
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        props={
            "ComparisonOperator": "GreaterThanThreshold",
            "EvaluationPeriods": 60,
            "ActionsEnabled": True,
            "AlarmActions": [{"Fn::ImportValue": Match.string_like_regexp("sns")}],
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


def test_api_lambda(lambda_template):
    lambda_template.resource_count_is("AWS::Lambda::Function", 1)
    lambda_template.resource_count_is("AWS::IAM::Role", 1)
    lambda_template.resource_count_is("AWS::IAM::Policy", 1)
    lambda_template.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "Handler": "spin_table_api.lambda_handler",
            "FunctionName": "test-lambda",
            "Timeout": 60,
        },
    )
