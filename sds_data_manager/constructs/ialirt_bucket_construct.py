"""Configure the IAlirt bucket."""

import aws_cdk as cdk
from aws_cdk import RemovalPolicy
from aws_cdk import aws_s3 as s3
from constructs import Construct


class IAlirtBucketConstruct(Construct):
    """Construct for IAlirt Ingest Bucket."""

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

        # This is the S3 bucket used to mount to the container.
        self.ialirt_bucket = s3.Bucket(
            self,
            "IAlirtBucket",
            bucket_name=f"ialirt-{account}",
            versioned=True,
            event_bridge_enabled=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )
