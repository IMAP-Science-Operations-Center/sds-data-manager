"""Configure the SDS API Manager."""

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secrets
from constructs import Construct

from .api_gateway_construct import ApiGateway


def add_stable_route(api, base_path, http_method, lambda_function, prefix_list):
    """Add routes to handle variations in path formatting.

    When the prefix of the route is passed in, any trailing '/' will be
    removed and checked for a starting '/'. This ensures that each route
    variation a user could call will result in a proper response.

    The two main routes handled and registered are a normalized (/api/upload)
    and a route with subpaths handled in proxy (/api/upload/{proxy+}).

    Parameters
    ----------
    api : obj
        The APIGateway stack.
    base_path : str
        The base route path (e.g., "/upload").
    http_method : str
        The HTTP method to allow (e.g., "GET", "POST").
    lambda_function : obj
        The lambda function.
    prefix_list : list[str]
        List of route prefixes.
    """
    # remove trailing backslash to circumvent error
    for prefix in prefix_list:
        clean = f"{prefix}{base_path}".rstrip("/")
        # add a starting '/' if not present
        if not clean.startswith("/"):
            clean = "/" + clean

        # the proxy route for subcommands
        proxy = f"{clean}/{{proxy+}}"
        # register both base (clean) and proxy routes
        for path in [clean, proxy]:
            api.add_route(
                route=path,
                http_method=http_method,
                lambda_function=lambda_function,
            )


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

        # landing page redirect
        landing_page_redirect_lambda = lambda_.Function(
            self,
            id="LandingPageRedirectLambda",
            function_name="landing-page-redirect",
            code=lambda_.InlineCode(
                """
            def lambda_handler(event, context):
                return {
                    "statusCode": 302,
                    "headers": {
                        "Location": "https://imap-processing.readthedocs.io/en/latest/data-access/index.html"
                    },
                "body": ""
                }
                """
            ),
            handler="index.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.seconds(5),
            memory_size=128,
            allow_public_subnet=True,
            layers=layers,
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

        # Redirect root '/' to the landing page
        api.add_route(
            route="/",
            http_method="GET",
            lambda_function=landing_page_redirect_lambda,
        )

        # basic route: /upload/{proxy+}
        # oauth2 JWT authorizer: /authorized/upload/{proxy+}
        # API key authorizer: /api-key/upload/{proxy+}
        auth_route_prefixes = ["", "/authorized", "/api-key"]

        # {proxy+} is used to allow for any pathParams after /upload/
        add_stable_route(api, "/upload", "GET", upload_api_lambda, auth_route_prefixes)

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

        # {proxy+} is used to allow for any pathParams after /query/
        add_stable_route(api, "/query", "GET", query_api_lambda, auth_route_prefixes)

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
            timeout=cdk.Duration.minutes(5),  # Reduce after issue #719 is done
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
        add_stable_route(api, "/download", "GET", download_api, auth_route_prefixes)

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
        for prefix in auth_route_prefixes:
            # {proxy+} is used to allow for any pathParams after /processing-jobs/
            api.add_route(
                route=f"{prefix}/processing-jobs",
                http_method="GET",
                lambda_function=batch_job_query_api_lambda,
            )

        # API to query batch job logs
        batch_logs_api_lambda = lambda_.Function(
            self,
            id="BatchLogsAPILambda",
            function_name="batch-logs-api-handler",
            code=code,
            handler="SDSCode.api_lambdas.batch_logs_api.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            allow_public_subnet=True,
            layers=layers,
        )
        for prefix in auth_route_prefixes:
            api.add_route(
                # {id+} is used to allow for any pathParams after /batch-logs/
                # This is needed because the log stream ID can contain slashes
                route=f"{prefix}/processing-logs/{{id+}}",
                http_method="GET",
                lambda_function=batch_logs_api_lambda,
            )

        batch_logs_read_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["logs:GetLogEvents"],
            resources=[
                "arn:aws:logs:*:*:log-group:/aws/batch/*",
            ],
        )
        batch_logs_api_lambda.add_to_role_policy(batch_logs_read_policy)

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
