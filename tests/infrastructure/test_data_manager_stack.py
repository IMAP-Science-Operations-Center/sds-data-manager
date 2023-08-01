# Standard
import pytest

# Installed
from aws_cdk.assertions import Match, Template

# Local
from sds_data_manager.stacks.opensearch_stack import OpenSearch
from sds_data_manager.stacks.sds_data_manager_stack import SdsDataManager


@pytest.fixture(scope="module")
def opensearch_stack(app, sds_id, env):
    stack_name = f"opensearch-{sds_id}"
    stack = OpenSearch(app, stack_name, sds_id, env=env)
    return stack


@pytest.fixture(scope="module")
def template(app, sds_id, opensearch_stack, env):
    stack_name = f"stack-{sds_id}"
    stack = SdsDataManager(app, stack_name, sds_id, opensearch_stack, env=env)
    template = Template.from_stack(stack)
    return template


def test_s3_bucket_resource_count(template):
    template.resource_count_is("AWS::S3::Bucket", 2)


def test_s3_data_bucket_resource_properties(template, sds_id):
    template.has_resource(
        "AWS::S3::Bucket",
        {
            "DeletionPolicy": "Delete",
            "UpdateReplacePolicy": "Delete",
        },
    )
    template.has_resource_properties(
        "AWS::S3::Bucket",
        props={
            "BucketName": f"sds-config-bucket-{sds_id}",
            "VersioningConfiguration": {"Status": "Enabled"},
            "PublicAccessBlockConfiguration": {"RestrictPublicBuckets": True},
        },
    )


def test_s3_config_bucket_resource_properties(template, sds_id):
    template.has_resource(
        "AWS::S3::Bucket",
        {
            "DeletionPolicy": "Delete",
            "UpdateReplacePolicy": "Delete",
        },
    )

    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "BucketName": f"sds-config-bucket-{sds_id}",
            "VersioningConfiguration": {"Status": "Enabled"},
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
        },
    )


def test_s3_bucket_policy_resource_count(template):
    template.resource_count_is("AWS::S3::BucketPolicy", 2)


def test_s3_data_bucket_policy_resource_properties(template):
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        props={
            "Bucket": {"Ref": Match.string_like_regexp("DataBucket*")},
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Action": ["s3:GetBucket*", "s3:List*", "s3:DeleteObject*"],
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


def test_s3_config_bucket_policy_resource_properties(template):
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "Bucket": {"Ref": Match.string_like_regexp("ConfigBucket*")},
            "PolicyDocument": {
                "Statement": [
                    {
                        "Action": ["s3:GetBucket*", "s3:List*", "s3:DeleteObject*"],
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
                                    Match.string_like_regexp("ConfigBucket*"),
                                    "Arn",
                                ]
                            },
                            {
                                "Fn::Join": [
                                    "",
                                    [
                                        {
                                            "Fn::GetAtt": [
                                                Match.string_like_regexp(
                                                    "ConfigBucket*"
                                                ),
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
                "Version": "2012-10-17",
            },
        },
    )


def test_custom_s3_auto_delete_resource_count(template):
    template.resource_count_is("Custom::S3AutoDeleteObjects", 2)


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


def test_config_bucket_custom_s3_auto_delete_resoource_count(template):
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
            "BucketName": {"Ref": Match.string_like_regexp("ConfigBucket*")},
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

    # There are 7 IAM Role expected resources with the same properties
    # confirm that all are found in the stack
    assert len(found_resources) == 7


def test_iam_policy_resource_count(template):
    template.resource_count_is("AWS::IAM::Policy", 6)


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
                                            Match.string_like_regexp(".*"),
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
                        "Resource": [
                            {
                                "Fn::Join": [
                                    "",
                                    [
                                        {
                                            "Fn::GetAtt": [
                                                Match.string_like_regexp(
                                                    "DataBucket.*"
                                                ),
                                                "Arn",
                                            ]
                                        },
                                        "/*",
                                    ],
                                ]
                            },
                            {
                                "Fn::Join": [
                                    "",
                                    [
                                        {
                                            "Fn::GetAtt": [
                                                Match.string_like_regexp(
                                                    "ConfigBucket.*"
                                                ),
                                                "Arn",
                                            ]
                                        },
                                        "/*",
                                    ],
                                ]
                            },
                        ],
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
                        "Effect": "Allow",
                        "Action": "es:ESHttp*",
                        "Resource": {
                            "Fn::Join": [
                                "",
                                [
                                    {
                                        "Fn::ImportValue": Match.string_like_regexp(
                                            "opensearch-.*:ExportsOutputFnGetAttSDSMetadataDomain.*"
                                        ),
                                    },
                                    "/*",
                                ],
                            ]
                        },
                    },
                    {
                        "Action": "s3:GetObject",
                        "Effect": "Allow",
                        "Resource": [
                            {
                                "Fn::Join": [
                                    "",
                                    [
                                        {
                                            "Fn::GetAtt": [
                                                Match.string_like_regexp(
                                                    "DataBucket.*"
                                                ),
                                                "Arn",
                                            ]
                                        },
                                        "/*",
                                    ],
                                ]
                            },
                            {
                                "Fn::Join": [
                                    "",
                                    [
                                        {
                                            "Fn::GetAtt": [
                                                Match.string_like_regexp(
                                                    "ConfigBucket.*"
                                                ),
                                                "Arn",
                                            ]
                                        },
                                        "/*",
                                    ],
                                ]
                            },
                        ],
                    },
                    {
                        "Effect": "Allow",
                    },
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
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "es:ESHttpGet",
                        "Resource": {
                            "Fn::Join": [
                                "",
                                [
                                    {
                                        "Fn::ImportValue": Match.string_like_regexp(
                                            "opensearch-.*:ExportsOutputFnGetAttSDSMetadataDomain.*"
                                        ),
                                    },
                                    "/*",
                                ],
                            ]
                        },
                    },
                    {
                        "Effect": "Allow",
                    },
                ],
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
                        "Action": "es:ESHttp*",
                        "Resource": {
                            "Fn::Join": [
                                "",
                                [
                                    {
                                        "Fn::ImportValue": Match.string_like_regexp(
                                            "opensearch-.*:ExportsOutputFnGetAttSDSMetadataDomain.*"
                                        ),
                                    },
                                    "/*",
                                ],
                            ]
                        },
                    },
                    {
                        "Effect": "Allow",
                        "Action": "s3:GetObject",
                        "Resource": [
                            {
                                "Fn::Join": [
                                    "",
                                    [
                                        {
                                            "Fn::GetAtt": [
                                                Match.string_like_regexp(
                                                    "DataBucket.*"
                                                ),
                                                "Arn",
                                            ]
                                        },
                                        "/*",
                                    ],
                                ]
                            },
                            {
                                "Fn::Join": [
                                    "",
                                    [
                                        {
                                            "Fn::GetAtt": [
                                                Match.string_like_regexp(
                                                    "ConfigBucket.*"
                                                ),
                                                "Arn",
                                            ]
                                        },
                                        "/*",
                                    ],
                                ]
                            },
                        ],
                    },
                ],
            },
            "PolicyName": Match.string_like_regexp(
                "DownloadQueryAPILambdaServiceRoleDefaultPolicy.*"
            ),
        },
    )


def test_custom_buck_deployment_iam_service_role_resource_properties(template):
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyName": Match.string_like_regexp(
                "CustomCDKBucketDeployment.*ServiceRoleDefaultPolicy.*"
            ),
            "PolicyDocument": {
                "Statement": [
                    {
                        "Action": ["s3:GetObject*", "s3:GetBucket*", "s3:List*"],
                        "Effect": "Allow",
                        "Resource": Match.any_value(),
                    },
                    {
                        "Action": [
                            "s3:GetObject*",
                            "s3:GetBucket*",
                            "s3:List*",
                            "s3:DeleteObject*",
                            "s3:PutObject",
                            "s3:PutObjectLegalHold",
                            "s3:PutObjectRetention",
                            "s3:PutObjectTagging",
                            "s3:PutObjectVersionTagging",
                            "s3:Abort*",
                        ],
                        "Effect": "Allow",
                        "Resource": [
                            {
                                "Fn::GetAtt": [
                                    Match.string_like_regexp("ConfigBucket*"),
                                    "Arn",
                                ]
                            },
                            {
                                "Fn::Join": [
                                    "",
                                    [
                                        {
                                            "Fn::GetAtt": [
                                                Match.string_like_regexp(
                                                    "ConfigBucket*"
                                                ),
                                                "Arn",
                                            ]
                                        },
                                        "/*",
                                    ],
                                ]
                            },
                        ],
                    },
                ],
                "Version": "2012-10-17",
            },
        },
    )


# We are no longer using lambda urls.
# def test_lambda_urls_resource_count(template):
#     template.resource_count_is("AWS::Lambda::Url", 3)
#
#
# def test_upload_api_lambda_url_resource_properties(template):
#     template.has_resource_properties(
#         "AWS::Lambda::Url",
#         {
#             "AuthType": "NONE",
#             "TargetFunctionArn": {
#                 "Fn::GetAtt": [Match.string_like_regexp("UploadAPILambda*"), "Arn"]
#             },
#             "Cors": {"AllowMethods": ["*"], "AllowOrigins": ["*"]},
#         },
#     )
#
#
# def test_query_api_lambda_url_resource_properties(template):
#     template.has_resource_properties(
#         "AWS::Lambda::Url",
#         {
#             "AuthType": "NONE",
#             "TargetFunctionArn": {
#                 "Fn::GetAtt": [Match.string_like_regexp("QueryAPILambda*"), "Arn"]
#             },
#             "Cors": {"AllowMethods": ["GET"], "AllowOrigins": ["*"]},
#         },
#     )
#
#
# def test_download_api_lambda_url_resource_properties(template):
#     template.has_resource_properties(
#         "AWS::Lambda::Url",
#         {
#             "AuthType": "NONE",
#             "TargetFunctionArn": {
#                 "Fn::GetAtt": [
#                     Match.string_like_regexp("DownloadQueryAPILambda*"),
#                     "Arn",
#                 ]
#             },
#             "Cors": {"AllowMethods": ["GET"], "AllowOrigins": ["*"]},
#         },
#     )


def test_lambda_function_resource_count(template):
    template.resource_count_is("AWS::Lambda::Function", 7)


def test_indexer_lambda_function_resource_properties(template, sds_id):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "FunctionName": f"file-indexer-{sds_id}",
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


def test_upload_api_lambda_function_resource_properties(template, sds_id):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "FunctionName": f"upload-api-handler-{sds_id}",
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


def test_query_api_lambda_function_resource_properties(template, sds_id):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "FunctionName": f"query-api-handler-{sds_id}",
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


def test_download_api_lambda_function_resource_properties(template, sds_id):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        props={
            "FunctionName": f"download-query-api-{sds_id}",
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


def test_custom_cdk_bucket_deployment_lambda_resource_properties(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Handler": "index.handler",
            "Runtime": "python3.9",
            "Timeout": 900,
            "Role": {
                "Fn::GetAtt": [
                    Match.string_like_regexp("CustomCDKBucketDeployment*"),
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


def test_lambda_layer_resource_count(template):
    template.resource_count_is("AWS::Lambda::LayerVersion", 1)


def test_custom_deploy_config_lambda_layer(template):
    template.has_resource_properties(
        "AWS::Lambda::LayerVersion",
        {
            "Content": {
                "S3Bucket": Match.any_value(),
                "S3Key": Match.string_like_regexp(".*.zip"),
            },
            "Description": "/opt/awscli/aws",
        },
    )
