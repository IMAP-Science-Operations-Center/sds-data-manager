"""Configure the data bucket."""

import aws_cdk as cdk
from aws_cdk import RemovalPolicy
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct


class DataBucketConstruct(Construct):
    """Construct for Data Bucket."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env: cdk.Environment,
        **kwargs,
    ) -> None:
        """Create data bucket.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        env : obj
            The environment
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)
        # Get the current account number so we can use it in the bucket names
        account = env.account

        # This is the S3 bucket used by upload_api_lambda
        self.data_bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=f"sds-data-{account}",
            versioned=True,
            event_bridge_enabled=True,
            removal_policy=RemovalPolicy.RETAIN,
            auto_delete_objects=False,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        s3_write_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:PutObject"],
            resources=[
                f"{self.data_bucket.bucket_arn}/*",
            ],
        )

        s3_replication_configuration_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:GetReplicationConfiguration", "s3:ListBucket"],
            resources=[f"{self.data_bucket.bucket_arn}"],
        )

        s3_replication_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:GetObjectVersionForReplication",
                "s3:GetObjectVersionAcl",
                "s3:GetObjectVersionTagging",
            ],
            resources=[f"{self.data_bucket.bucket_arn}/*"],
        )

        # Rather than depending on the deploy in another account through CDK,
        # we can assume the backup bucket already exists and go from here.
        # Consisting of the source account number (this account) and "backup"
        backup_bucket_name = f"sds-data-{account}-backup"

        s3_backup_bucket_items_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:ReplicateObject",
                "s3:ReplicateDelete",
                "s3:ReplicateTags",
                "s3:GetObject",
                "s3:List*",
            ],
            resources=[f"arn:aws:s3:::{backup_bucket_name}/*"],
        )

        s3_backup_bucket_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:List*"],
            resources=[f"arn:aws:s3:::{backup_bucket_name}"],
        )

        # Create role for backup bucket in the backup account
        backup_role = iam.Role(
            self,
            "BackupRole",
            assumed_by=iam.ServicePrincipal("s3.amazonaws.com"),
            description="Role for getting permissions to \
                        replicate out of S3 bucket in this account.",
            role_name="BackupRole",
        )

        backup_role.add_to_policy(s3_replication_configuration_policy)
        backup_role.add_to_policy(s3_replication_policy)
        backup_role.add_to_policy(s3_backup_bucket_items_policy)
        backup_role.add_to_policy(s3_backup_bucket_policy)
        backup_role.add_to_policy(s3_write_policy)

        # Add replication configuration to the data bucket
        # accesses the raw cloudformation L1 constructs which is necessary
        # because the l2 constructs do not support replication configuration
        bucket_cf: s3.CfnBucket = self.data_bucket.node.default_child
        bucket_cf.replication_configuration = s3.CfnBucket.ReplicationConfigurationProperty(  # noqa: E501
            role=backup_role.role_arn,
            rules=[
                s3.CfnBucket.ReplicationRuleProperty(
                    id="ReplicateToBackup",
                    status="Enabled",
                    priority=1,
                    delete_marker_replication=s3.CfnBucket.DeleteMarkerReplicationProperty(
                        status="Disabled"
                    ),
                    destination=s3.CfnBucket.ReplicationDestinationProperty(
                        bucket=f"arn:aws:s3:::{backup_bucket_name}",
                        # TODO: Enable deep archive storage class for backups that
                        #       are accessed infrequently.
                        # storage_class="DEEP_ARCHIVE",
                    ),
                    filter=s3.CfnBucket.ReplicationRuleFilterProperty(prefix=""),
                )
            ],
        )
