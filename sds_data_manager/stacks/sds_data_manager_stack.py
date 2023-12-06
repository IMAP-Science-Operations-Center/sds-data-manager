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
    aws_s3_deployment as s3_deploy,
)
from aws_cdk import (
    aws_secretsmanager as secrets,
)
from constructs import Construct

from .api_gateway_stack import ApiGateway

# Local
from .dynamodb_stack import DynamoDB
from .opensearch_stack import OpenSearch


class SdsDataManager(Stack):
    """Stack for Data Management."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        opensearch: OpenSearch,
        dynamodb_stack: DynamoDB,
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
        opensearch: OpenSearch
            This class depends on opensearch, which is built with opensearch_stack.py
        dynamodb_stack: DynamoDb
            This class depends on dynamodb_stack, which is built with
            opensearch_stack.py
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
            "ConfigBucket",
            bucket_name=f"sds-config-bucket-{account}",
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
            "DeployConfig",
            sources=[
                s3_deploy.Source.asset(
                    str(
                        pathlib.Path(__file__).parent.joinpath("..", "config").resolve()
                    )
                )
            ],
            destination_bucket=config_bucket,
        )

        ########### OpenSearch Snapshot Storage
        snapshot_bucket = s3.Bucket(
            self,
            "SnapshotBucket",
            bucket_name=f"sds-opensearch-snapshot-{account}",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        s3_write_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:PutObject"],
            resources=[
                f"{self.data_bucket.bucket_arn}/*",
                f"{snapshot_bucket.bucket_arn}/*",
            ],
        )
        s3_read_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:GetObject"],
            resources=[
                f"{self.data_bucket.bucket_arn}/*",
                f"{config_bucket.bucket_arn}/*",
                f"{snapshot_bucket.bucket_arn}/*",
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

        dynamodb_write_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["dynamodb:PutItem"],
            resources=["*"],
        )

        snapshot_role_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:ListBucket",
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
            ],
            resources=[
                f"{snapshot_bucket.bucket_arn}",
                f"{snapshot_bucket.bucket_arn}/*",
            ],
        )

        ########### ROLES
        snapshot_role = iam.Role(
            self, "SnapshotRole", assumed_by=iam.ServicePrincipal("es.amazonaws.com")
        )
        snapshot_role.add_to_policy(snapshot_role_policy)

        step_function_execution_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW, actions=["states:StartExecution"], resources=["*"]
        )

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
                "OS_ADMIN_USERNAME": "master-user",
                "OS_DOMAIN": opensearch.sds_metadata_domain.domain_endpoint,
                "OS_PORT": "443",
                "METADATA_INDEX": "metadata",
                "DATA_TRACKER_INDEX": "data_tracker",
                "DYNAMODB_TABLE": dynamodb_stack.table_name,
                "S3_DATA_BUCKET": self.data_bucket.s3_url_for_object(),
                "S3_CONFIG_BUCKET_NAME": f"sds-config-bucket-{account}",
                "S3_SNAPSHOT_BUCKET_NAME": f"sds-opensearch-snapshot-{account}",
                "SNAPSHOT_ROLE_ARN": snapshot_role.role_arn,
                "SNAPSHOT_REPO_NAME": "snapshot-repo",
                "SECRET_ID": opensearch.secret_name,
                "REGION": opensearch.region,
            },
        )

        indexer_lambda.add_event_source(
            aws_lambda_event_sources.S3EventSource(
                self.data_bucket, events=[s3.EventType.OBJECT_CREATED]
            )
        )
        indexer_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        # Adding Opensearch permissions
        indexer_lambda.add_to_role_policy(opensearch.opensearch_all_http_permissions)
        # Adding s3 read permissions to get config.json
        indexer_lambda.add_to_role_policy(s3_read_policy)
        # Adding dynamodb write permissions
        indexer_lambda.add_to_role_policy(dynamodb_write_policy)
        # Adding step function execution policy
        indexer_lambda.add_to_role_policy(step_function_execution_policy)

        # Add permissions for Lambda to access OpenSearch
        indexer_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["es:*"],
                resources=[f"{opensearch.sds_metadata_domain.domain_arn}/*"],
            )
        )

        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", db_secret_name
        )
        rds_secret.grant_read(grantee=indexer_lambda)

        # PassRole allows services to assign AWS roles to resources and services
        # in this account. The OpenSearch snapshot role is invoked within the Lambda to
        # interact with OpenSearch, it is provided to lambda via an Environmental
        # variable in the lambda definition
        indexer_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["iam:PassRole"],
                resources=[snapshot_role.role_arn],
            )
        )

        opensearch_secret = secrets.Secret.from_secret_name_v2(
            self, "opensearch_secret", opensearch.secret_name
        )
        opensearch_secret.grant_read(grantee=indexer_lambda)

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
                "S3_CONFIG_BUCKET_NAME": f"sds-config-bucket-{account}",
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
                "OS_ADMIN_USERNAME": "master-user",
                "OS_DOMAIN": opensearch.sds_metadata_domain.domain_endpoint,
                "OS_PORT": "443",
                "OS_INDEX": "metadata",
                "SECRET_ID": opensearch.secret_name,
                "REGION": region,
            },
        )
        query_api_lambda.add_to_role_policy(opensearch.opensearch_read_only_policy)

        api.add_route(
            route="query",
            http_method="GET",
            lambda_function=query_api_lambda,
        )

        opensearch_secret.grant_read(grantee=query_api_lambda)

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
        download_query_api.add_to_role_policy(
            opensearch.opensearch_all_http_permissions
        )
        download_query_api.add_to_role_policy(s3_read_policy)

        api.add_route(
            route="download",
            http_method="GET",
            lambda_function=download_query_api,
        )
