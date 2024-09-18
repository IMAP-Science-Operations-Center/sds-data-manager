"""Test the backup bucket stack."""

import pytest
from aws_cdk.assertions import Match, Template

from sds_data_manager.constructs.backup_bucket_construct import BackupBucket


@pytest.fixture()
def template(stack):
    """Return a template for the backup bucket stack."""
    BackupBucket(
        stack,
        construct_id="BackupBucket-test",
        source_account="0",
    )

    template = Template.from_stack(stack)

    return template


def test_s3_bucket_resource_count(template):
    """Ensure that the template has a S3 bucket."""
    template.resource_count_is("AWS::S3::Bucket", 1)


def test_s3_config_bucket_resource_properties(template):
    """Ensure that the template has the appropriate bucket properties."""
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
            "BucketName": "sds-data-0-backup",
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
    """Ensure that the template has the appropriate bucket policy."""
    template.resource_count_is("AWS::S3::BucketPolicy", 1)


def test_s3_data_bucket_policy_resource_properties(template):
    """Ensure the template has the appropriate policy resource properties."""
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        props={
            "Bucket": {"Ref": Match.string_like_regexp("BackupDataBucket*")},
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
                                    Match.string_like_regexp("BackupDataBucket*"),
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
                                                    "BackupDataBucket*"
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
                        "Action": [
                            "s3:ReplicateObject",
                            "s3:ReplicateDelete",
                            "s3:GetObject",
                        ],
                        "Effect": "Allow",
                        "Principal": {
                            "AWS": Match.string_like_regexp(".*SdsDataManager.*")
                        },
                        "Resource": {
                            "Fn::Join": [
                                "",
                                [
                                    {
                                        "Fn::GetAtt": [
                                            Match.string_like_regexp(
                                                "BackupDataBucket*"
                                            ),
                                            "Arn",
                                        ]
                                    },
                                    "/*",
                                ],
                            ]
                        },
                    },
                    {
                        "Action": [
                            "s3:List*",
                            "s3:GetBucketVersioning",
                            "s3:PutBucketVersioning",
                        ],
                        "Effect": "Allow",
                        "Principal": {
                            "AWS": Match.string_like_regexp(".*SdsDataManager.*")
                        },
                        "Resource": {
                            "Fn::GetAtt": [
                                Match.string_like_regexp("BackupDataBucket*"),
                                "Arn",
                            ]
                        },
                    },
                ],
            },
        },
    )
