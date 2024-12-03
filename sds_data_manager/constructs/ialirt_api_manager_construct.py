"""Configure the I-ALiRT API Manager."""

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

from .api_gateway_construct import ApiGateway


class IalirtApiManager(Construct):
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
        layers : list
            List of Lambda layers arns
        kwargs : dict
            Keyword arguments
        """
        super().__init__(scope, construct_id, **kwargs)

        s3_read_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:ListBucket", "s3:GetObject"],
            resources=[
                data_bucket.bucket_arn,
                f"{data_bucket.bucket_arn}/*",
            ],
        )

        # query API lambda
        query_api_lambda = lambda_.Function(
            self,
            id="IAlirtCodeQueryAPILambda",
            function_name="ialirt-query-api-handler",
            code=code,
            handler="IAlirtCode.ialirt_query_api.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            allow_public_subnet=True,
            vpc=vpc,
            environment={
                "S3_BUCKET": data_bucket.bucket_name,
                "REGION": env.region,
            },
            layers=layers,
            architecture=lambda_.Architecture.ARM_64,
        )

        query_api_lambda.add_to_role_policy(s3_read_policy)

        api.add_route(
            route="ialirt-log-query",
            http_method="GET",
            lambda_function=query_api_lambda,
        )

        # download API lambda
        download_api = lambda_.Function(
            self,
            id="IAlirtCodeDownloadAPILambda",
            function_name="ialirt-download-api-handler",
            code=code,
            handler="SDSCode.api_lambdas.download_api.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            environment={
                "S3_BUCKET": data_bucket.bucket_name,
                "REGION": env.region,
            },
            layers=layers,
            architecture=lambda_.Architecture.ARM_64,
        )

        download_api.add_to_role_policy(s3_read_policy)

        api.add_route(
            route="ialirt-log-download",
            http_method="GET",
            lambda_function=download_api,
            use_path_params=True,
        )
