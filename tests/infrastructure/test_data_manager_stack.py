# Standard
import pytest
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds

# Installed
from aws_cdk.assertions import Match, Template

from sds_data_manager.stacks.api_gateway_stack import ApiGateway
from sds_data_manager.stacks.database_stack import SdpDatabase

# Local
from sds_data_manager.stacks.networking_stack import NetworkingStack
from sds_data_manager.stacks.sds_data_manager_stack import SdsDataManager


@pytest.fixture(scope="module")
def networking_stack(app, env):
    networking = NetworkingStack(app, "Networking", env=env)
    return networking


@pytest.fixture(scope="module")
def database_stack(app, networking_stack, env):
    rds_size = "SMALL"
    rds_class = "BURSTABLE3"
    rds_storage = 200
    database_stack = SdpDatabase(
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
    return database_stack


@pytest.fixture(scope="module")
def template(app, networking_stack, database_stack, env):
    apigw = ApiGateway(
        app,
        construct_id="ApigwTest",
    )

    stack = SdsDataManager(
        app,
        "sds-data-manager-test",
        api=apigw,
        env=env,
        db_secret_name="0123456789",
        vpc=networking_stack.vpc,
        vpc_subnets=database_stack.rds_subnet_selection,
        rds_security_group=networking_stack.rds_security_group,
    )
    template = Template.from_stack(stack)

    return template


def test_s3_bucket_resource_count(template):
    template.resource_count_is("AWS::S3::Bucket", 1)


def test_s3_bucket_policy_resource_count(template):
    template.resource_count_is("AWS::S3::BucketPolicy", 1)


def test_s3_data_bucket_policy_resource_properties(template):
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        props={
            "Bucket": {"Ref": Match.string_like_regexp("DataBucket*")},
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Action": [
                            "s3:PutBucketPolicy",
                            "s3:GetBucket*",
                            "s3:List*",
                            "s3:DeleteObject*",
                        ],
                        "Effect": "Allow",
                        "Principal": {
                            "AWS": {
                                "Fn::GetAtt": [
                                    Match.string_like_regexp(
                                        "CustomS3AutoDeleteObjectsCustomResourceProviderRole*"
                                    ),
                                    "Arn",
                                ]
                            }
                        },
                        "Resource": [
                            {
                                "Fn::GetAtt": [
                                    Match.string_like_regexp("DataBucket*"),
                                    "Arn",
                                ]
                            },
                            {
                                "Fn::Join": [
                                    "",
                                    [
                                        {
                                            "Fn::GetAtt": [
                                                Match.string_like_regexp("DataBucket*"),
                                                "Arn",
                                            ]
                                        },
                                        "/*",
                                    ],
                                ]
                            },
                        ],
                    }
                ],
            },
        },
    )


def test_custom_s3_auto_delete_resource_count(template):
    template.resource_count_is("Custom::S3AutoDeleteObjects", 1)


def test_data_bucket_custom_s3_auto_delete_resource_properties(template):
    template.has_resource_properties(
        "Custom::S3AutoDeleteObjects",
        {
            "ServiceToken": {
                "Fn::GetAtt": [
                    Match.string_like_regexp(
                        "CustomS3AutoDeleteObjectsCustomResourceProviderHandler*"
                    ),
                    "Arn",
                ]
            },
            "BucketName": {"Ref": Match.string_like_regexp("DataBucket*")},
        },
    )


def test_custom_s3_bucket_notifications_resource_properties(template):
    template.resource_count_is("Custom::S3BucketNotifications", 1)

    template.has_resource_properties(
        "Custom::S3BucketNotifications",
        {
            "ServiceToken": {
                "Fn::GetAtt": [
                    Match.string_like_regexp("BucketNotificationsHandler*"),
                    "Arn",
                ]
            },
            "BucketName": {"Ref": Match.string_like_regexp("DataBucket*")},
            "NotificationConfiguration": {
                "LambdaFunctionConfigurations": [
                    {
                        "Events": ["s3:ObjectCreated:*"],
                        "LambdaFunctionArn": {
                            "Fn::GetAtt": [
                                Match.string_like_regexp("IndexerLambda*"),
                                "Arn",
                            ]
                        },
                    }
                ]
            },
            "Managed": True,
        },
    )


def test_iam_roles_resource_count(template):
    template.resource_count_is("AWS::IAM::Role", 7)


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

    # There are 6 IAM Role expected resources with the same properties
    # confirm that all are found in the stack
    assert len(found_resources) == 6


def test_iam_policy_resource_count(template):
    template.resource_count_is("AWS::IAM::Policy", 5)


def test_uploadapilambda_iam_policy_resource_properties(template):
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "s3:PutObject",
                        "Resource": {
                            "Fn::Join": [
                                "",
                                [
                                    {
                                        "Fn::GetAtt": [
                                            Match.string_like_regexp("DataBucket.*"),
                                            "Arn",
                                        ]
                                    },
                                    "/*",
                                ],
                            ]
                        },
                    },
                    {
                        "Effect": "Allow",
                        "Action": "s3:GetObject",
                        "Resource": {
                            "Fn::Join": [
                                "",
                                [
                                    {
                                        "Fn::GetAtt": [
                                            Match.string_like_regexp("DataBucket.*"),
                                            "Arn",
                                        ]
                                    },
                                    "/*",
                                ],
                            ]
                        },
                    },
                ],
            },
            "PolicyName": Match.string_like_regexp(
                "UploadAPILambdaServiceRoleDefaultPolicy.*"
            ),
        },
    )


def test_indexer_lambda_iam_policy_resource_properties(template):
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Action": [
                            "secretsmanager:GetSecretValue",
                            "secretsmanager:DescribeSecret",
                        ],
                        "Effect": "Allow",
                        "Resource": {
                            "Fn::Join": [
                                "",
                                [
                                    "arn:",
                                    {"Ref": "AWS::Partition"},
                                    Match.string_like_regexp(
                                        ":secretsmanager:.*:secret:.*"
                                    ),
                                ],
                            ]
                        },
                    }
                ],
            },
            "PolicyName": Match.string_like_regexp(
                "IndexerLambdaServiceRoleDefaultPolicy.*"
            ),
        },
    )


def test_bucket_notification_iam_policy_resource_properties(template):
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "s3:PutBucketNotification",
                        "Resource": "*",
                    }
                ],
            },
            "PolicyName": Match.string_like_regexp("BucketNotificationsHandler*"),
        },
    )


def test_queryapilambda_iam_policy_resource_properties(template):
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Version": "2012-10-17",
            },
            "PolicyName": Match.string_like_regexp(
                "QueryAPILambdaServiceRoleDefaultPolicy.*"
            ),
        },
    )


def test_downloadquerylambda_iam_policy_resource_properties(template):
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "s3:GetObject",
                        "Resource": {
                            "Fn::Join": [
                                "",
                                [
                                    {
                                        "Fn::GetAtt": [
                                            Match.string_like_regexp("DataBucket.*"),
                                            "Arn",
                                        ]
                                    },
                                    "/*",
                                ],
                            ]
                        },
                    },
                ],
            },
            "PolicyName": Match.string_like_regexp(
                "DownloadQueryAPILambdaServiceRoleDefaultPolicy.*"
            ),
        },
    )


def test_lambda_function_resource_count(template):
    template.resource_count_is("AWS::Lambda::Function", 6)


def test_indexer_lambda_function_resource_properties(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "FunctionName": "file-indexer",
            "Runtime": "python3.9",
            "Handler": "SDSCode.indexer.lambda_handler",
            "MemorySize": 1000,
            "Timeout": 15 * 60,
            "Role": {
                "Fn::GetAtt": [
                    Match.string_like_regexp("IndexerLambdaServiceRole*"),
                    "Arn",
                ]
            },
        },
    )


def test_upload_api_lambda_function_resource_properties(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "FunctionName": "upload-api-handler",
            "Runtime": "python3.9",
            "Handler": "SDSCode.upload_api.lambda_handler",
            "MemorySize": 1000,
            "Timeout": 15 * 60,
            "Role": {
                "Fn::GetAtt": [
                    Match.string_like_regexp("UploadAPILambdaServiceRole*"),
                    "Arn",
                ]
            },
        },
    )


def test_query_api_lambda_function_resource_properties(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "FunctionName": "query-api-handler",
            "Runtime": "python3.9",
            "Handler": "SDSCode.queries.lambda_handler",
            "MemorySize": 1000,
            "Timeout": 60,
            "Role": {
                "Fn::GetAtt": [
                    Match.string_like_regexp("QueryAPILambdaServiceRole*"),
                    "Arn",
                ]
            },
        },
    )


def test_download_api_lambda_function_resource_properties(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "FunctionName": "download-query-api",
            "Runtime": "python3.9",
            "Handler": "SDSCode.download_query_api.lambda_handler",
            "Timeout": 60,
            "Role": {
                "Fn::GetAtt": [
                    Match.string_like_regexp("DownloadQueryAPILambdaServiceRole*"),
                    "Arn",
                ]
            },
        },
    )


def test_aws_bucket_notification_lambda_function_resource_properties(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "Handler": "index.handler",
            "Runtime": "python3.9",
            "Timeout": 300,
            "Role": {
                "Fn::GetAtt": [
                    Match.string_like_regexp("BucketNotificationsHandler*"),
                    "Arn",
                ]
            },
        },
    )


def test_custom_s3_auto_delete_lambda_function_resource_properties(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "Handler": "index.handler",
            "Runtime": "nodejs18.x",
            "Timeout": 900,
            "MemorySize": 128,
            "Role": {
                "Fn::GetAtt": [
                    Match.string_like_regexp(
                        "CustomS3AutoDeleteObjectsCustomResourceProviderRole*"
                    ),
                    "Arn",
                ]
            },
        },
    )


# This is now only to add an eventsource to the indexer lambda.
# The 3 others were being used by the
# lambda urls (download, query, upload) and so no longer exist.
def test_lambda_permission_resource_count(template):
    template.resource_count_is("AWS::Lambda::Permission", 1)


def test_indexer_lambda_permission_resource_properties(template, account):
    template.has_resource_properties(
        "AWS::Lambda::Permission",
        {
            "Action": "lambda:InvokeFunction",
            "FunctionName": {
                "Fn::GetAtt": [Match.string_like_regexp("IndexerLambda*"), "Arn"]
            },
            "Principal": "s3.amazonaws.com",
            "SourceAccount": account,
            "SourceArn": {
                "Fn::GetAtt": [Match.string_like_regexp("DataBucket*"), "Arn"]
            },
        },
    )


# Note: these tests don't work because in the previous version of the code,
# we created lambda_.FunctionUrl objects
# which granted permissions for lambda function URLs to be invoked.
# def test_upload_api_lambda_permission_resource_properties(template):
#     template.has_resource_properties(
#         "AWS::Lambda::Permission",
#         {
#             "Action": "lambda:InvokeFunctionUrl",
#             "FunctionName": {
#                 "Fn::GetAtt": [Match.string_like_regexp("UploadAPILambda*"), "Arn"]
#             },
#             "Principal": "*",
#             "FunctionUrlAuthType": "NONE",
#         },
#     )
#
#
# def test_query_api_lambda_permission_resource_properties(template):
#     template.has_resource_properties(
#         "AWS::Lambda::Permission",
#         {
#             "Action": "lambda:InvokeFunctionUrl",
#             "FunctionName": {
#                 "Fn::GetAtt": [Match.string_like_regexp("QueryAPILambda*"), "Arn"]
#             },
#             "Principal": "*",
#             "FunctionUrlAuthType": "NONE",
#         },
#     )
#
#
# def test_download_api_lambda_permission_resource_properties(template):
#     template.has_resource_properties(
#         "AWS::Lambda::Permission",
#         {
#             "Action": "lambda:InvokeFunctionUrl",
#             "FunctionName": {
#                 "Fn::GetAtt": [
#                     Match.string_like_regexp("DownloadQueryAPILambda*"),
#                     "Arn",
#                 ]
#             },
#             "Principal": "*",
#             "FunctionUrlAuthType": "NONE",
#         },
#     )


def test_backup_role_iam_policy_resource_properties(template):
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "s3:PutBucketNotification",
                        "Resource": "*",
                    }
                ],
            },
            "PolicyName": Match.string_like_regexp("BucketNotificationsHandler*"),
        },
    )
