"""Configure the backup bucket."""

from aws_cdk import RemovalPolicy
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct


class BackupBucket(Construct):
    """Creates the destination bucket for data backups.

    It can be run in the same account as SdsDataManager, or in a separate
    account. The source_account is a required parameter. This source account
    should be the AWS account for the source bucket.

    For replication to work, you also need to deploy SdsDataManager and create
    the source bucket and replication role. Then, you need to manually update
    the role_arn variable with the replication role created.

    Finally, you need to set up the replication rule in the **source** account.
    To do this, go to the source bucket and click the "Management" tab.
    Under the "Replication Rules" section, create a replication rule. Specify
    your bucket, select the IAM Role "BackupRole" created in SdsDataManager, and
    save the rule. This rule can be set up after both source and destination
    stacks are deployed.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        source_account: str,
        **kwargs,
    ) -> None:
        """BackupBucketConstruct.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        source_account : str
            Account number for the source S3 bucket
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        # FOR NOW: Deploy other stack, update this name with the created role.
        role_arn = (
            "arn:aws:iam::449431850278:"
            "role/SdsDataManager-mh-dev-BackupRoleF43CFD90-FXUPB8TEP0P0"
        )

        # This is the S3 bucket used by upload_api_lambda
        backup_bucket = s3.Bucket(
            self,
            "BackupDataBucket",
            bucket_name=f"sds-data-{source_account}-backup",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["cognito-idp:*"],
            resources=["*"],
        )

        replicate_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:ReplicateObject", "s3:ReplicateDelete", "s3:GetObject"],
            resources=[f"{backup_bucket.bucket_arn}/*"],
        )
        replicate_policy.add_arn_principal(role_arn)

        versioning_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:List*", "s3:GetBucketVersioning", "s3:PutBucketVersioning"],
            resources=[f"{backup_bucket.bucket_arn}"],
        )
        versioning_policy.add_arn_principal(role_arn)

        backup_bucket.add_to_resource_policy(replicate_policy)
        backup_bucket.add_to_resource_policy(versioning_policy)
