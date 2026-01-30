"""Configure the I-ALiRT API Manager."""

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

from .api_gateway_construct import ApiGateway
from .sds_api_manager_construct import add_stable_route


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
        algorithm_table: ddb.Table,
        data_table: ddb.Table,
        account_name: str = "dev",
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
        algorithm_table : obj
            The algorithm DynamoDB table
        data_table : object
            The data DynamoDB Table
        account_name : str
            The account name. Eg. 'prod' or 'dev'
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

        auth_route_prefixes = ["", "/authorized", "/api-key"]
        if account_name == "prod":
            restricted_route_prefixes = ["/api-key", "/authorized"]
        else:
            restricted_route_prefixes = auth_route_prefixes

        # log query API lambda
        log_query_api_lambda = lambda_.Function(
            self,
            id="IAlirtCodeLogQueryAPILambda",
            function_name="ialirt-log-query-api-handler",
            code=code,
            handler="IAlirtCode.ialirt_log_query_api.lambda_handler",
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
        )

        log_query_api_lambda.add_to_role_policy(s3_read_policy)

        api.add_route(
            route="/ialirt-log-query",
            http_method="GET",
            lambda_function=log_query_api_lambda,
        )

        # packets query API lambda
        packets_query_api_lambda = lambda_.Function(
            self,
            id="IAlirtCodePacketsQueryAPILambda",
            function_name="ialirt-packets-query-api-handler",
            code=code,
            handler="IAlirtCode.ialirt_packets_query_api.lambda_handler",
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
        )

        packets_query_api_lambda.add_to_role_policy(s3_read_policy)

        api.add_route(
            route="/ialirt-packet-query",
            http_method="GET",
            lambda_function=packets_query_api_lambda,
        )

        # Queries latest realtime filename.
        # Realtime file contains tcp connection and packet creation information.
        realtime_query_api_lambda = lambda_.Function(
            self,
            id="IAlirtCodeRealtimeQueryAPILambda",
            function_name="ialirt-realtime-query-api-handler",
            code=code,
            handler="IAlirtCode.ialirt_realtime_query_api.lambda_handler",
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
        )

        realtime_query_api_lambda.add_to_role_policy(s3_read_policy)

        api.add_route(
            route="/ialirt-realtime-query",
            http_method="GET",
            lambda_function=realtime_query_api_lambda,
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
        )

        download_api.add_to_role_policy(s3_read_policy)

        # Deny access to logs/ and raw_records/ for the download_api lambda role
        data_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                effect=iam.Effect.DENY,
                actions=["s3:GetObject"],
                principals=[iam.ArnPrincipal(download_api.role.role_arn)],
                resources=[
                    f"{data_bucket.bucket_arn}/raw_records/*",
                ],
            )
        )

        add_stable_route(
            api,
            "/ialirt-download",
            "GET",
            download_api,
            ["", "/authorized", "/api-key"],
        )
        add_stable_route(
            api,
            "/ialirt-download",
            "HEAD",
            download_api,
            ["", "/authorized", "/api-key"],
        )

        # catalog API lambda
        catalog_api = lambda_.Function(
            self,
            id="IAlirtCatalogAPILambda",
            function_name="ialirt-catalog-api-handler",
            code=code,
            handler="IAlirtCode.ialirt_catalog_api.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            environment={
                "S3_BUCKET": data_bucket.bucket_name,
                "REGION": env.region,
            },
            layers=layers,
        )

        catalog_api.add_to_role_policy(s3_read_policy)

        api.add_route(
            route="/ialirt-catalog",
            http_method="GET",
            lambda_function=catalog_api,
        )

        ialirt_db_query_handler = lambda_.Function(
            self,
            "IAlirtDbQueryApiHandler",
            function_name="ialirt-db-query-handler",
            code=code,
            handler="IAlirtCode.ialirt_db_query_api.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            # Lambda allocates CPU proportionally to memory,
            # and DynamoDB queries are often CPU-bound due to
            # JSON parsing and network serialization.
            memory_size=2048,  # MB
            environment={
                "ALGORITHM_TABLE": algorithm_table.table_name,
                "REGION": env.region,
            },
        )

        # Grant the lambda function read/write permissions on the DynamoDB table.
        algorithm_table.grant_read_data(ialirt_db_query_handler)

        add_stable_route(
            api,
            "/ialirt-db-query",
            "GET",
            ialirt_db_query_handler,
            restricted_route_prefixes,
        )

        ialirt_db_query_formatted_handler = lambda_.Function(
            self,
            "IAlirtDbQueryApiFormattedHandler",
            function_name="ialirt-db-query-formatted-handler",
            code=code,
            handler="IAlirtCode.ialirt_data_query_api.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            # Lambda allocates CPU proportionally to memory,
            # and DynamoDB queries are often CPU-bound due to
            # JSON parsing and network serialization.
            memory_size=2048,
            environment={
                "DATA_TABLE": data_table.table_name,
                "REGION": env.region,
            },
        )

        # Grant the lambda function read/write permissions on the DynamoDB table.
        data_table.grant_read_data(ialirt_db_query_formatted_handler)

        add_stable_route(
            api,
            "/space-weather",
            "GET",
            ialirt_db_query_formatted_handler,
            restricted_route_prefixes,
        )
