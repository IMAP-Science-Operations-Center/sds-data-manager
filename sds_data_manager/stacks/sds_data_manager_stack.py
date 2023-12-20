# Standard
import pathlib

# Installed
import aws_cdk as cdk
from aws_cdk import (
    RemovalPolicy,
    Stack,
    aws_lambda_event_sources,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_lambda_python_alpha as lambda_alpha_,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_secretsmanager as secrets,
)
from constructs import Construct

from .api_gateway_stack import ApiGateway


class SdsDataManager(Stack):
    """Stack for Data Management."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        api: ApiGateway,
        env: cdk.Environment,
        db_secret_name: str,
        vpc: ec2.Vpc,
        vpc_subnets,
        rds_security_group,
        **kwargs,
    ) -> None:
        """SdsDataManagerStack

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        api: ApiGateway
            This class has created API resources. This function uses it to add
            route that points to targe Lambda.
        """
        super().__init__(scope, construct_id, env=env, **kwargs)
        # Get the current account number so we can use it in the bucket names
        account = env.account
        region = env.region

        # This is the S3 bucket used by upload_api_lambda
        self.data_bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=f"sds-data-{account}",
            versioned=True,
            event_bridge_enabled=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        s3_write_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:PutObject"],
            resources=[
                f"{self.data_bucket.bucket_arn}/*",
            ],
        )
        s3_read_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:GetObject"],
            resources=[
                f"{self.data_bucket.bucket_arn}/*",
            ],
        )
        iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["cognito-idp:*"],
            resources=["*"],
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

        indexer_lambda = lambda_alpha_.PythonFunction(
            self,
            id="IndexerLambda",
            function_name="file-indexer",
            entry=str(
                pathlib.Path(__file__).parent.joinpath("..", "lambda_code").resolve()
            ),
            index="SDSCode/indexer.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=cdk.Duration.minutes(15),
            memory_size=1000,
            allow_public_subnet=True,
            vpc=vpc,
            vpc_subnets=vpc_subnets,
            security_groups=[rds_security_group],
            environment={
                "DATA_TRACKER_INDEX": "data_tracker",
                "S3_DATA_BUCKET": self.data_bucket.s3_url_for_object(),
            },
        )

        indexer_lambda.add_event_source(
            aws_lambda_event_sources.S3EventSource(
                self.data_bucket, events=[s3.EventType.OBJECT_CREATED]
            )
        )
        indexer_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", db_secret_name
        )
        rds_secret.grant_read(grantee=indexer_lambda)

        # upload API lambda
        upload_api_lambda = lambda_alpha_.PythonFunction(
            self,
            id="UploadAPILambda",
            function_name="upload-api-handler",
            entry=str(
                pathlib.Path(__file__).parent.joinpath("..", "lambda_code").resolve()
            ),
            index="SDSCode/upload_api.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=cdk.Duration.minutes(15),
            memory_size=1000,
            environment={
                "S3_BUCKET": self.data_bucket.s3_url_for_object(),
            },
        )
        upload_api_lambda.add_to_role_policy(s3_write_policy)
        upload_api_lambda.add_to_role_policy(s3_read_policy)
        upload_api_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        api.add_route(
            route="upload",
            http_method="GET",
            lambda_function=upload_api_lambda,
        )

        # query API lambda
        query_api_lambda = lambda_alpha_.PythonFunction(
            self,
            id="QueryAPILambda",
            function_name="query-api-handler",
            entry=str(
                pathlib.Path(__file__).parent.joinpath("..", "lambda_code").resolve()
            ),
            index="SDSCode/queries.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            environment={
                "REGION": region,
            },
        )

        api.add_route(
            route="query",
            http_method="GET",
            lambda_function=query_api_lambda,
        )

        # download query API lambda
        download_query_api = lambda_alpha_.PythonFunction(
            self,
            id="DownloadQueryAPILambda",
            function_name="download-query-api",
            entry=str(
                pathlib.Path(__file__).parent.joinpath("..", "lambda_code").resolve()
            ),
            index="SDSCode/download_query_api.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=cdk.Duration.seconds(60),
        )

        download_query_api.add_to_role_policy(s3_read_policy)

        api.add_route(
            route="download",
            http_method="GET",
            lambda_function=download_query_api,
        )
