# Standard
import pytest

# Installed
import boto3
from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template

# Local
from sds_data_manager.stacks import opensearch_stack


@pytest.fixture(scope="module")
def template():

    session = boto3.Session()

    account = session.client('sts').get_caller_identity().get('Account')
    region = session.region_name

    env = Environment(account=account, region=region)
    app = App()

    sds_id = "sdsid-test"
    stack = opensearch_stack.OpenSearch(app, f"OpenSearch-{sds_id}",
                                              sds_id, env=env)
    template = Template.from_stack(stack)
    return template


def test_secrets_manager_resource_count(template):
    template.resource_count_is("AWS::SecretsManager::Secret", 1)


def test_secrets_manager_resource_properties(template):
    template.has_resource(
        "AWS::SecretsManager::Secret",
        {
            "DeletionPolicy": "Delete",
            "UpdateReplacePolicy": "Delete",
        },
    )


def test_iam_roles_resource_count(template):
    template.resource_count_is("AWS::IAM::Role", 1)

def test_expected_properties_for_iam_roles(template):
    found_resources = template.find_resources(
        "AWS::IAM::Role",
        {
            "Properties": {
                "AssumeRolePolicyDocument": {
                    "Statement": [
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                        }
                    ],
                    "Version": "2012-10-17",
                }
            }
        },
    )

    # There is 1 IAM Role expected resources with the same properties
    # confirm that all are found in the stack
    assert len(found_resources) == 1





def test_opensearch_domain_resource_count(template):
    template.resource_count_is("AWS::OpenSearchService::Domain", 1)


def test_opensearch_domain_resource_properties(template, sds_id):
    template.has_resource_properties(
        "AWS::OpenSearchService::Domain",
        {
            "DomainName": f"sdsmetadatadomain-{sds_id}",
            "EngineVersion": "OpenSearch_1.3",
            "ClusterConfig": {"InstanceType": "t3.small.search", "InstanceCount": 1},
            "EBSOptions": {"EBSEnabled": True, "VolumeSize": 10, "VolumeType": "gp2"},
            "NodeToNodeEncryptionOptions": {"Enabled": True},
            "EncryptionAtRestOptions": {"Enabled": True},
        },
    )


def test_custom_cloudwatch_log_resource_policy_count(template):
    template.resource_count_is("Custom::CloudwatchLogResourcePolicy", 1)


def test_log_groups_resource_count(template):
    template.resource_count_is("AWS::Logs::LogGroup", 3)


def test_sdsmetadatadomain_slow_search_logs_resource_properties(template):
    template.has_resource(
        "AWS::Logs::LogGroup",
        {
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
        },
    )
    template.has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": 30})


def test_sdsmetadatadomain_slow_index_logs_resource_properties(template):
    template.has_resource(
        "AWS::Logs::LogGroup",
        {
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
        },
    )
    template.has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": 30})


def test_sdsmetadatadomain_app_logs_resource_properties(template):
    template.has_resource(
        "AWS::Logs::LogGroup",
        {
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
        },
    )
    template.has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": 30})





def test_custom_opensearch_access_policy_resource_count(template):
    template.resource_count_is("Custom::OpenSearchAccessPolicy", 1)


def test_custom_opensearch_access_policy_resource_properties(template):
    template.has_resource(
        "Custom::OpenSearchAccessPolicy",
        {
            "DeletionPolicy": "Delete",
            "UpdateReplacePolicy": "Delete",
        },
    )

    template.has_resource_properties(
        "Custom::OpenSearchAccessPolicy",
        {
            "ServiceToken": {"Fn::GetAtt": [Match.string_like_regexp("AWS*"), "Arn"]},
            "Create": {
                "Fn::Join": [
                    "",
                    [
                        '{"action":"updateDomainConfig","service":"OpenSearch","parameters":{"DomainName":"',
                        {"Ref": Match.string_like_regexp("SDSMetadataDomain*")},
                        '","AccessPolicies":"{\\"Statement\\":[{\\"Action\\":\\"es:*\\",\\"Effect\\":\\"Allow\\",\\"Principal\\":{\\"AWS\\":\\"*\\"},\\"Resource\\":\\"',
                        {
                            "Fn::GetAtt": [
                                Match.string_like_regexp("SDSMetadataDomain*"),
                                "Arn",
                            ]
                        },
                        '/*\\"}],\\"Version\\":\\"2012-10-17\\"}"},"outputPaths":["DomainConfig.AccessPolicies"],"physicalResourceId":{"id":"',
                        {"Ref": Match.string_like_regexp("SDSMetadataDomain*")},
                        'AccessPolicy"}}',
                    ],
                ]
            },
            "Update": {
                "Fn::Join": [
                    "",
                    [
                        '{"action":"updateDomainConfig","service":"OpenSearch","parameters":{"DomainName":"',
                        {"Ref": Match.string_like_regexp("SDSMetadataDomain*")},
                        '","AccessPolicies":"{\\"Statement\\":[{\\"Action\\":\\"es:*\\",\\"Effect\\":\\"Allow\\",\\"Principal\\":{\\"AWS\\":\\"*\\"},\\"Resource\\":\\"',
                        {
                            "Fn::GetAtt": [
                                Match.string_like_regexp("SDSMetadataDomain*"),
                                "Arn",
                            ]
                        },
                        '/*\\"}],\\"Version\\":\\"2012-10-17\\"}"},"outputPaths":["DomainConfig.AccessPolicies"],"physicalResourceId":{"id":"',
                        {"Ref": Match.string_like_regexp("SDSMetadataDomain*")},
                        'AccessPolicy"}}',
                    ],
                ]
            },
            "InstallLatestAwsSdk": True,
        },
    )


def test_log_groups_resource_count(template):
    template.resource_count_is("AWS::Logs::LogGroup", 3)


def test_sdsmetadatadomain_slow_search_logs_resource_properties(template):
    template.has_resource(
        "AWS::Logs::LogGroup",
        {
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
        },
    )
    template.has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": 30})


def test_sdsmetadatadomain_slow_index_logs_resource_properties(template):
    template.has_resource(
        "AWS::Logs::LogGroup",
        {
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
        },
    )
    template.has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": 30})


def test_sdsmetadatadomain_app_logs_resource_properties(template):
    template.has_resource(
        "AWS::Logs::LogGroup",
        {
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
        },
    )
    template.has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": 30})


def test_iam_roles_resource_count(template):
    template.resource_count_is("AWS::IAM::Role", 1)


def test_expected_properties_for_iam_roles(template):
    found_resources = template.find_resources(
        "AWS::IAM::Role",
        {
            "Properties": {
                "AssumeRolePolicyDocument": {
                    "Statement": [
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                        }
                    ],
                    "Version": "2012-10-17",
                }
            }
        },
    )

    # There are 1 IAM Role expected resources with the same properties
    # confirm that all are found in the stack
    assert len(found_resources) == 1


def test_iam_policy_resource_count(template):
    template.resource_count_is("AWS::IAM::Policy", 2)


def test_sdsmetadatadomain_esloggroup_iam_policy_resource_properties(template, sds_id):
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "logs:PutResourcePolicy",
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": "logs:DeleteResourcePolicy",
                        "Resource": "*",
                    },
                ],
            },
            "PolicyName": Match.string_like_regexp(
                f"SDSMetadataDomainsdsidtestESLogGroupPolicyc*"
            ),
        },
    )


def test_sdsmetadatadomain_accesspolicy_iam_policy_resource_properties(template):
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "es:UpdateDomainConfig",
                        "Resource": {
                            "Fn::GetAtt": [
                                Match.string_like_regexp("SDSMetadataDomain*"),
                                "Arn",
                            ]
                        },
                    }
                ],
            },
            "PolicyName": Match.string_like_regexp(
                "SDSMetadataDomainsdsidtestAccessPolicyCustomResourcePolicy*"
            ),
        },
    )


def test_lambda_function_resource_count(template):
    template.resource_count_is("AWS::Lambda::Function", 1)


def test_aws_lambda_function_resource_properties(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "Handler": "index.handler",
            "Runtime": "nodejs14.x",
            "Timeout": 120,
            "Role": {
                "Fn::GetAtt": [
                    Match.string_like_regexp("AWS.*ServiceRole.*"),
                    "Arn",
                ]
            },
        },
    )