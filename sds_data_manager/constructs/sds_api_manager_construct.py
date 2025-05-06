"""Configure the SDS API Manager."""

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secrets
from constructs import Construct

from .api_gateway_construct import ApiGateway


class SdsApiManager(Construct):
    """Construct for API Management."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        code: lambda_.Code,
        api: ApiGateway,
        env: cdk.Environment,
        data_bucket,
        vpc,
        rds_security_group,
        db_secret_name: str,
        layers: list,
        **kwargs,
    ) -> None:
        """Initialize the SdsApiManagerConstruct.

        Parameters
        ----------
        scope : obj
            Parent construct
        construct_id : str
            A unique string identifier for this construct
        code : lambda_.Code
            Lambda code bundle
        api : obj
            The APIGateway stack
        env : obj
            The CDK environment
        data_bucket : obj
            The data bucket
        vpc : obj
            The VPC
        rds_security_group : obj
            The RDS security group
        db_secret_name : str
            The DB secret name
        layers : list
            List of Lambda layers arns
        kwargs : dict
            Keyword arguments
        """
        super().__init__(scope, construct_id, **kwargs)

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

        # upload API lambda
        upload_api_lambda = lambda_.Function(
            self,
            id="UploadAPILambda",
            function_name="upload-api-handler",
            code=code,
            handler="SDSCode.api_lambdas.upload_api.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            allow_public_subnet=True,
            vpc=vpc,
            security_groups=[rds_security_group],
            environment={
                "S3_BUCKET": data_bucket.bucket_name,
                "SECRET_NAME": db_secret_name,
                "REGION": env.region,
            },
            layers=layers,
        )
        upload_api_lambda.add_to_role_policy(s3_write_policy)
        upload_api_lambda.add_to_role_policy(s3_read_policy)
        upload_api_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        # {proxy+} is used to allow for any pathParams after /upload/
        api.add_route(
            route="/upload/{proxy+}",
            http_method="GET",
            lambda_function=upload_api_lambda,
        )
        api.add_route(
            route="/authorized/upload/{proxy+}",
            http_method="GET",
            lambda_function=upload_api_lambda,
        )

        # query API lambda
        query_api_lambda = lambda_.Function(
            self,
            id="QueryAPILambda",
            function_name="query-api-handler",
            code=code,
            handler="SDSCode.api_lambdas.query_api.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            allow_public_subnet=True,
            vpc=vpc,
            security_groups=[rds_security_group],
            environment={
                "REGION": env.region,
                "SECRET_NAME": db_secret_name,
            },
            layers=layers,
        )

        api.add_route(
            route="/query",
            http_method="GET",
            lambda_function=query_api_lambda,
        )
        api.add_route(
            route="/authorized/query",
            http_method="GET",
            lambda_function=query_api_lambda,
        )

        # SPICE query API lambda
        spice_query_api_lambda = lambda_.Function(
            self,
            id="SPICEQueryAPILambda",
            function_name="spice-query-api-handler",
            code=code,
            handler="SDSCode.api_lambdas.spice_query_api.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            allow_public_subnet=True,
            vpc=vpc,
            security_groups=[rds_security_group],
            environment={
                "REGION": env.region,
                "SECRET_NAME": db_secret_name,
            },
            layers=layers,
        )

        api.add_route(
            route="/spice-query",
            http_method="GET",
            lambda_function=spice_query_api_lambda,
        )

        # SPICE metakernel API lambda
        spice_metakernel_api_lambda = lambda_.Function(
            self,
            id="SPICEMetakernelAPILambda",
            function_name="spice-metakernel-api-handler",
            code=code,
            handler="SDSCode.api_lambdas.spice_metakernel_api.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            allow_public_subnet=True,
            vpc=vpc,
            security_groups=[rds_security_group],
            environment={
                "REGION": env.region,
                "SECRET_NAME": db_secret_name,
            },
            layers=layers,
        )

        api.add_route(
            route="/metakernel",
            http_method="GET",
            lambda_function=spice_metakernel_api_lambda,
        )

        # download API lambda
        download_api = lambda_.Function(
            self,
            id="DownloadAPILambda",
            function_name="download-api-handler",
            code=code,
            handler="SDSCode.api_lambdas.download_api.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            environment={
                "S3_BUCKET": data_bucket.bucket_name,
                "REGION": env.region,
            },
            layers=layers,
        )

        download_api.add_to_role_policy(s3_read_policy)

        # {proxy+} is used to allow for any pathParams after /download/
        api.add_route(
            route="/download/{proxy+}",
            http_method="GET",
            lambda_function=download_api,
        )
        api.add_route(
            route="/authorized/download/{proxy+}",
            http_method="GET",
            lambda_function=download_api,
        )

        universal_spin_table_handler = lambda_.Function(
            self,
            id="universal-spin-table-api-handler",
            function_name="universal-spin-table-api-handler",
            code=code,
            handler="SDSCode.api_lambdas.spin_table_api.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            allow_public_subnet=True,
            vpc=vpc,
            security_groups=[rds_security_group],
            environment={
                "SECRET_NAME": db_secret_name,
            },
            layers=layers,
        )

        # API to query batch job information
        batch_job_query_api_lambda = lambda_.Function(
            self,
            id="BatchJobQueryAPILambda",
            function_name="batch-job-query-api-handler",
            code=code,
            handler="SDSCode.api_lambdas.batch_job_query_api.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            allow_public_subnet=True,
            vpc=vpc,
            security_groups=[rds_security_group],
            environment={
                "SECRET_NAME": db_secret_name,
            },
            layers=layers,
        )
        api.add_route(
            route="/batch-job",
            http_method="GET",
            lambda_function=batch_job_query_api_lambda,
        )
        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", db_secret_name
        )
        rds_secret.grant_read(grantee=universal_spin_table_handler)
        rds_secret.grant_read(grantee=query_api_lambda)
        rds_secret.grant_read(grantee=spice_query_api_lambda)
        rds_secret.grant_read(grantee=spice_metakernel_api_lambda)
        rds_secret.grant_read(grantee=upload_api_lambda)
        rds_secret.grant_read(grantee=batch_job_query_api_lambda)

        api.add_route(
            route="/spin_table",
            http_method="GET",
            lambda_function=universal_spin_table_handler,
        )
