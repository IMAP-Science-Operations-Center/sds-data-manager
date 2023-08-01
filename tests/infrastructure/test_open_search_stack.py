# Standard
import pytest

# Installed
from aws_cdk.assertions import Match, Template

# Local
from sds_data_manager.stacks.opensearch_stack import OpenSearch


@pytest.fixture(scope="module")
def template(app, sds_id, env):
    stack_name = f"opensearch-{sds_id}"
    stack = OpenSearch(app, stack_name, sds_id, env=env)
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
    template.has_resource_properties(
        "AWS::IAM::Role",
        {
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
        },
    )


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
                        '","AccessPolicies":"{\\"Statement\\":[{\\"Action\\":\\"es:*\\",\\"Effect\\":\\"Allow\\",'
                        '\\"Principal\\":{\\"AWS\\":\\"*\\"},\\"Resource\\":\\"',
                        {
                            "Fn::GetAtt": [
                                Match.string_like_regexp("SDSMetadataDomain*"),
                                "Arn",
                            ]
                        },
                        '/*\\"}],\\"Version\\":\\"2012-10-17\\"}"},"outputPaths":['
                        '"DomainConfig.AccessPolicies"],"physicalResourceId":{"id":"',
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
                        '","AccessPolicies":"{\\"Statement\\":[{\\"Action\\":\\"es:*\\",'
                        '\\"Effect\\":\\"Allow\\",\\"Principal\\":{\\"AWS\\":\\"*\\"},\\"Resource\\":\\"',
                        {
                            "Fn::GetAtt": [
                                Match.string_like_regexp("SDSMetadataDomain*"),
                                "Arn",
                            ]
                        },
                        '/*\\"}],\\"Version\\":\\"2012-10-17\\"}"},'
                        '"outputPaths":["DomainConfig.AccessPolicies"],"physicalResourceId":{"id":"',
                        {"Ref": Match.string_like_regexp("SDSMetadataDomain*")},
                        'AccessPolicy"}}',
                    ],
                ]
            },
            "InstallLatestAwsSdk": True,
        },
    )


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
                "SDSMetadataDomainsdsidtestESLogGroupPolicyc*"
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
            "Runtime": "nodejs18.x",
            "Timeout": 120,
            "Role": {
                "Fn::GetAtt": [
                    Match.string_like_regexp("AWS.*ServiceRole.*"),
                    "Arn",
                ]
            },
        },
    )
