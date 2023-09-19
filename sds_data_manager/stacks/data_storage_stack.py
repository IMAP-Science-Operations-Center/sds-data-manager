"""Data Storage Stack"""
from constructs import Construct
from aws_cdk import (
    Stack,
    Environment,
    RemovalPolicy,
    aws_s3 as s3
)


class DataStorageStack(Stack):
    """Data Storage Resources"""

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 sds_id: str,
                 env: Environment,
                 **kwargs) -> None:
        """DataStorageStack constructor

        Parameters
        ----------
        scope : App
        construct_id : str
        sds_id : str
            Name suffix for stack
        env : Environment
            Account and region
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        #TODO: removal policy may change
        removal_policy = RemovalPolicy.DESTROY
        self.bucket_name = f"archive-{sds_id}"

        # Bucket for processed data
        self.archive_bucket = s3.Bucket(self, f"ArchiveBucket-{sds_id}",
                                        bucket_name=self.bucket_name,
                                        versioned=True,
                                        event_bridge_enabled=True,
                                        block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                                        removal_policy=removal_policy)