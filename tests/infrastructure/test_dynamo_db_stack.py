import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match as match
from aws_cdk.assertions import Template

from sds_data_manager.stacks.dynamodb_stack import DynamoDB


@pytest.fixture()
def on_demand_dynamodb(sds_id="test"):
    app = cdk.App()

    stack_name = f"on-demand-stack-{sds_id}"
    stack = DynamoDB(
        app,
        stack_name,
        sds_id,
        table_name=f"table-{sds_id}",
        partition_key="filename",
        sort_key="instrument",
        env=None,
    )
    template = Template.from_stack(stack)
    return template


@pytest.fixture()
def provisioned_dynamodb(sds_id="test"):
    app = cdk.App()

    stack_name = f"provisioned-stack-{sds_id}"
    stack = DynamoDB(
        app,
        stack_name,
        sds_id,
        table_name=f"table-{sds_id}",
        partition_key="filename",
        sort_key="instrument",
        env=None,
        on_demand=False,
        write_capacity=100,
        read_capacity=100,
    )
    template = Template.from_stack(stack)
    return template


def test_table_count(on_demand_dynamodb, provisioned_dynamodb):
    on_demand_dynamodb.resource_count_is("AWS::DynamoDB::Table", 1)
    provisioned_dynamodb.resource_count_is("AWS::DynamoDB::Table", 1)


def test_billing_mode(on_demand_dynamodb, provisioned_dynamodb):
    on_demand_dynamodb.has_resource_properties(
        "AWS::DynamoDB::Table", {"BillingMode": "PAY_PER_REQUEST"}
    )
    # If provisioned, it doesn't have BillingMode in resource properties. It instead has
    # read and write capacity units.
    # Note: match.any_value() matches any non-null value at the target.
    provisioned_dynamodb.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "ProvisionedThroughput": {
                "ReadCapacityUnits": match.any_value(),
                "WriteCapacityUnits": match.any_value(),
            }
        },
    )


def test_point_in_time_recovery(on_demand_dynamodb, provisioned_dynamodb):
    on_demand_dynamodb.has_resource_properties(
        "AWS::DynamoDB::Table",
        {"PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True}},
    )
    provisioned_dynamodb.has_resource_properties(
        "AWS::DynamoDB::Table",
        {"PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True}},
    )


def test_delete_table_policy(on_demand_dynamodb, provisioned_dynamodb):
    on_demand_dynamodb.has_resource(
        "AWS::DynamoDB::Table",
        {
            "DeletionPolicy": "Delete",
            "UpdateReplacePolicy": "Delete",
        },
    )
    provisioned_dynamodb.has_resource(
        "AWS::DynamoDB::Table",
        {
            "DeletionPolicy": "Delete",
            "UpdateReplacePolicy": "Delete",
        },
    )
