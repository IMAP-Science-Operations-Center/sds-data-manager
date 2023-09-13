"""Data Storage Stack"""
# Installed
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
                 sid: str,
                 env: Environment,
                 **kwargs) -> None:
        """DataStorageStack constructor

        Parameters
        ----------
        scope : App
        construct_id : str
        env : Environment
            Account and region
        name_suffix : str or None
            String to append to the end of any globally unique resource names.
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        #TODO: may change
        removal_policy = RemovalPolicy.DESTROY

        # Bucket for processed data
        self.archive_bucket = s3.Bucket(self, "ArchiveBucket",
                                        bucket_name=f"archive-{sid}",
                                        versioned=True,
                                        block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                                        removal_policy=removal_policy)