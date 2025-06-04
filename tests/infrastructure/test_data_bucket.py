"""Test the data bucket stack."""

import pytest
from aws_cdk.assertions import Template

from sds_data_manager.constructs.data_bucket_construct import DataBucketConstruct


@pytest.fixture
def template(stack, env):
    """Return a template for the data bucket stack."""
    data_stack = DataBucketConstruct(stack, "data-bucket", env=env)
    data_stack.setup_backup_replication()
    template = Template.from_stack(stack)

    return template


def test_s3_bucket(template):
    """Ensure the template has the appropriate amount of buckets."""
    template.resource_count_is("AWS::S3::Bucket", 1)
    # Ensure the template has S3 auto delete enabled
    template.resource_count_is("Custom::S3AutoDeleteObjects", 0)
    # The auto_delete_objects = False, disabling
    # the CDK's default behavior of attaching a BucketPolicy that
    # grants a custom Lambda permission to auto-delete objects
    resources = [
        r
        for r in template.to_json()["Resources"].values()
        if r["Type"] == "AWS::S3::BucketPolicy"
    ]
    assert not resources, (
        "Expected no BucketPolicy resources since auto_delete_objects is False"
    )

    # Ensure that the template has the appropriate bucket policy
    template.resource_count_is("AWS::S3::BucketPolicy", 0)
    # Ensure that the template has the appropriate IAM role
    template.has_resource_properties("AWS::IAM::Role", props={"RoleName": "BackupRole"})
