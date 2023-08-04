from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from sds_data_manager.stacks.lambda_stack import LambdaWithDockerImageStack
from sds_data_manager.stacks.step_function_stack import ProcessingStepFunctionStack


@pytest.fixture(scope="module")
def step_function(app, sds_id, env):
    app = cdk.App()

    stack_name = f"processing-step-function-stack-{sds_id}"
    stack = ProcessingStepFunctionStack(
        app, stack_name, sds_id, env=env, dynamodb_table_name="test-table"
    )
    template = Template.from_stack(stack)
    return template


@pytest.fixture(scope="module")
def lambda_stack(app, sds_id, env):
    app = cdk.App()

    stack_name = f"processing-lambda-stack-{sds_id}"
    # Set path of main folder for lambda code.
    lambda_code_main_folder = (
        f"{Path(__file__).parent}/../../sds_data_manager/lambda_images/"
    )

    # Set processing lambda code path. This path should contain Dockerfile.
    imap_processing_lambda_code_path = (
        f"{lambda_code_main_folder}/imap_processing_lambda/"
    )
    stack = LambdaWithDockerImageStack(
        app,
        stack_name,
        lambda_name=f"lambda-{sds_id}",
        managed_policy_names=[
            "service-role/AWSLambdaBasicExecutionRole",
            "AmazonS3FullAccess",
            "AmazonDynamoDBFullAccess",
        ],
        lambda_code_folder=imap_processing_lambda_code_path,
        lambda_environment_vars={
            "DYNAMODB_TABLE": "test-table",
        },
    )
    template = Template.from_stack(stack)
    return template


def test_step_function_state_machine(step_function):
    step_function.resource_count_is("AWS::StepFunctions::StateMachine", 1)


def test_step_function_state_machine_has_definition(step_function):
    step_function.has_resource_properties(
        "AWS::StepFunctions::StateMachine", {"DefinitionString": Match.any_value()}
    )


def test_step_function_has_role(step_function):
    step_function.resource_count_is("AWS::IAM::Role", 1)


# Test lambda that processing step function calls
def test_lambda_stack_has_role(lambda_stack):
    lambda_stack.resource_count_is("AWS::IAM::Role", 1)


def test_lambda_has_aws_managed_policy(lambda_stack):
    lambda_stack.has_resource_properties(
        "AWS::IAM::Role",
        props={
            "ManagedPolicyArns": Match.array_equals(
                [
                    {
                        "Fn::Join": [
                            "",
                            [
                                "arn:",
                                {"Ref": "AWS::Partition"},
                                ":iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                            ],
                        ]
                    },
                    {
                        "Fn::Join": [
                            "",
                            [
                                "arn:",
                                {"Ref": "AWS::Partition"},
                                ":iam::aws:policy/AmazonS3FullAccess",
                            ],
                        ]
                    },
                    {
                        "Fn::Join": [
                            "",
                            [
                                "arn:",
                                {"Ref": "AWS::Partition"},
                                ":iam::aws:policy/AmazonDynamoDBFullAccess",
                            ],
                        ]
                    },
                ]
            )
        },
    )


def test_lambda_stack_has_lambda(lambda_stack):
    lambda_stack.resource_count_is("AWS::Lambda::Function", 1)


def test_lambda_has_environment_variable_set(lambda_stack):
    lambda_stack.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "Environment": {
                "Variables": {
                    "DYNAMODB_TABLE": "test-table",
                }
            }
        },
    )
