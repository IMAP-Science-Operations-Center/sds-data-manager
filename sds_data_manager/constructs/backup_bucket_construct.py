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

    For replication to work, you also need to deploy SDCStack and create
    the source bucket and replication role.
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

        # NOTE: This requires the source account to have this specific BackupRole
        #       which we create in the data_bucket_construct
        role_arn = f"arn:aws:iam::{source_account}:role/BackupRole"

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
