# Standard
import pathlib

# Installed
import aws_cdk as cdk
from aws_cdk import (
    Stack,
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
    aws_secretsmanager as secrets,
)
from constructs import Construct

from .api_gateway_stack import ApiGateway


class SdsApiManager(Stack):
    """Stack for API Management."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        api: ApiGateway,
        env: cdk.Environment,
        data_bucket,
        vpc,
        rds_security_group,
        db_secret_name: str,
        **kwargs,
    ) -> None:
        """SdsApiManagerStack

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        """
        super().__init__(scope, construct_id, env=env, **kwargs)
        # Get the current region
        region = env.region

        s3_write_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:PutObject"],
            resources=[
                f"{data_bucket.bucket_arn}/*",
            ],
        )
        s3_read_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:GetObject"],
            resources=[
                f"{data_bucket.bucket_arn}/*",
            ],
        )
        lambda_code_directory = pathlib.Path(__file__).parent.parent / "lambda_code"
        lambda_code_directory_str = str(lambda_code_directory.resolve())

        # upload API lambda
        upload_api_lambda = lambda_alpha_.PythonFunction(
            self,
            id="UploadAPILambda",
            function_name="upload-api-handler",
            entry=lambda_code_directory_str,
            index="SDSCode/upload_api.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=cdk.Duration.minutes(15),
            memory_size=1000,
            environment={
                "S3_BUCKET": data_bucket.s3_url_for_object(),
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
            entry=lambda_code_directory_str,
            index="SDSCode/queries.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            allow_public_subnet=True,
            vpc=vpc,
            security_groups=[rds_security_group],
            environment={
                "REGION": region,
                "SECRET_NAME": db_secret_name,
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
            entry=lambda_code_directory_str,
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

        spin_table_code = lambda_code_directory / "spin_table_api.py"

        universal_spin_table_handler = lambda_alpha_.PythonFunction(
            self,
            id="universal-spin-table-api-handler",
            function_name="universal-spin-table-api-handler",
            entry=str(spin_table_code.parent / "SDSCode"),  # This gives folder path
            index=str(spin_table_code.name),  # This gives file name
            handler="lambda_handler",  # This points to function inside the file
            runtime=lambda_.Runtime.PYTHON_3_11,
            timeout=cdk.Duration.minutes(1),
            memory_size=512,
            vpc=vpc,
            security_groups=[rds_security_group],
            allow_public_subnet=True,
        )

        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", db_secret_name
        )
        rds_secret.grant_read(grantee=universal_spin_table_handler)
        rds_secret.grant_read(grantee=query_api_lambda)

        api.add_route(
            route="spin_table",
            http_method="GET",
            lambda_function=universal_spin_table_handler,
        )
