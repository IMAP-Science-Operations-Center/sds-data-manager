"""Cron job to poll an external HTTPS endpoint for a contact schedule XML file."""

from aws_cdk import Duration, RemovalPolicy, aws_s3
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class IalirtScheduleFetchConstruct(Construct):
    """Construct for polling an external contact schedule endpoint."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        ialirt_bucket: aws_s3.Bucket,
        docker_path: str = "sds_data_manager/lambda_code",
        schedule_endpoint_url: str = "",
        cert_secret_name: str = "",
        key_secret_name: str = "",
        **kwargs,
    ) -> None:
        """Poll external HTTPS endpoint for contact schedule XML.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        ialirt_bucket : aws_s3.Bucket
            The data bucket.
        docker_path : str
            Path to the Dockerfile.
        schedule_endpoint_url : str
            The HTTPS endpoint URL to poll for the schedule XML.
        cert_secret_name : str
            The Secrets Manager secret name for the SSL client certificate.
        key_secret_name : str
            The Secrets Manager secret name for the SSL client key.
        kwargs : dict
            Keyword arguments.
        """
        super().__init__(scope, construct_id, **kwargs)

        ialirt_schedule_fetch_lambda = self.create_schedule_fetch_lambda(
            ialirt_bucket,
            docker_path,
            schedule_endpoint_url,
            cert_secret_name,
            key_secret_name,
        )
        self.create_event_rule(ialirt_schedule_fetch_lambda)

    def create_schedule_fetch_lambda(
        self,
        ialirt_bucket: aws_s3.Bucket,
        docker_path: str,
        schedule_endpoint_url: str,
        cert_secret_name: str,
        key_secret_name: str,
    ) -> lambda_.DockerImageFunction:
        """Create and return the Lambda function."""
        lambda_role = iam.Role(
            self,
            "IalirtScheduleFetchConstructRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        s3_write_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:PutObject"],
            resources=[f"{ialirt_bucket.bucket_arn}/*"],
        )

        ialirt_schedule_fetch_lambda = lambda_.DockerImageFunction(
            self,
            id="IalirtScheduleFetchLambda",
            code=lambda_.DockerImageCode.from_image_asset(
                docker_path,
                file="IAlirtCode/Dockerfile.schedule_fetch",
            ),
            function_name="ialirt-schedule-fetch",
            timeout=Duration.minutes(1),
            memory_size=256,
            role=lambda_role,
            environment={
                "S3_BUCKET": ialirt_bucket.bucket_name,
                "SCHEDULE_ENDPOINT_URL": schedule_endpoint_url,
                "CERT_SECRET_NAME": cert_secret_name,
                "KEY_SECRET_NAME": key_secret_name,
            },
        )

        ialirt_schedule_fetch_lambda.add_to_role_policy(s3_write_policy)

        # Grant Lambda read access to the cert and key secrets
        for secret_name in [cert_secret_name, key_secret_name]:
            if secret_name:
                secret = secretsmanager.Secret.from_secret_name_v2(
                    self, f"Secret-{secret_name}", secret_name
                )
                secret.grant_read(ialirt_schedule_fetch_lambda)

        ialirt_schedule_fetch_lambda.apply_removal_policy(RemovalPolicy.DESTROY)

        return ialirt_schedule_fetch_lambda

    def create_event_rule(
        self, ialirt_schedule_fetch_lambda: lambda_.DockerImageFunction
    ) -> None:
        """Create the event rule to trigger Lambda once per hour."""
        rule = events.Rule(
            self,
            "IalirtHourlyScheduleFetchRule",
            schedule=events.Schedule.rate(Duration.hours(1)),
        )
        rule.add_target(targets.LambdaFunction(ialirt_schedule_fetch_lambda))
