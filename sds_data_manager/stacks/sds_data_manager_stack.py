# Standard
import pathlib
# Installed
from constructs import Construct
from aws_cdk import (
    Stack,
    Environment,
    RemovalPolicy,
    aws_iam as iam,
    aws_s3 as s3,
    aws_s3_deployment as s3_deploy,
)


class SdsDataManager(Stack):
    """Stack for Data Management."""
    def __init__(self, scope: Construct,
                 construct_id: str,
                 sds_id: str,
                 env: Environment,
                 **kwargs) -> None:
        """SdsDataManagerStack

        Parameters
        ----------
        scope : App
        construct_id : str
        sds_id: str
        env : Environment
            Account and region
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        # This is the S3 bucket used by upload_api_lambda
        self.data_bucket = s3.Bucket(
            self, f"DataBucket-{sds_id}",
            bucket_name=f"sds-data-{sds_id}",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # Confirm that a config.json file exists in the expected
        # location before S3 upload
        if (
            not pathlib.Path(__file__)
            .parent.joinpath("..", "config", "config.json")
            .resolve()
            .exists()
        ):
            raise RuntimeError(
                "sds_data_manager/config directory must contain config.json"
            )

        # S3 bucket where the configurations will be stored
        config_bucket = s3.Bucket(
            self,
            f"ConfigBucket-{sds_id}",
            bucket_name=f"sds-config-{sds_id}-lcs",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # Upload all files in the config directory to the S3 config bucket.
        # This directory should contain a config.json file that will
        # be used for indexing files into the data bucket.
        s3_deploy.BucketDeployment(
            self,
            f"DeployConfig-{sds_id}",
            sources=[
                s3_deploy.Source.asset(
                    str(pathlib.Path(__file__).parent.joinpath("..", "config").resolve())
                )
            ],
            destination_bucket=config_bucket,
        )

        self.s3_write_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:PutObject"],
            resources=[f"{self.data_bucket.bucket_arn}/*"],
        )
        self.s3_read_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:GetObject"],
            resources=[f"{self.data_bucket.bucket_arn}/*", f"{config_bucket.bucket_arn}/*"],
        )
        iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["cognito-idp:*"],
            resources=["*"],
        )
