"""Cron job to create ialirt coverage files."""

from aws_cdk import Duration, RemovalPolicy, aws_s3
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class IalirtCoverageConstruct(Construct):
    """Construct for ialirt coverage."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        ialirt_bucket: aws_s3.Bucket,
        docker_path: str = "sds_data_manager/lambda_code",
        data_access_url: str = "",
        **kwargs,
    ) -> None:
        """Create ialirt coverage files.

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
        data_access_url : str, optional
            The data access URL to use for this job, by default the empty string.
            You should set this to the appropriate API endpoint, e.g.
            https://api.dev.imap-mission.com
        kwargs : dict
            Keyword arguments.

        """
        super().__init__(scope, construct_id, **kwargs)

        # Create Lambda Function
        ialirt_coverage_lambda = self.create_coverage_lambda(
            ialirt_bucket, docker_path, data_access_url
        )
        self.create_event_rule(ialirt_coverage_lambda)

    def create_coverage_lambda(
        self,
        ialirt_bucket: aws_s3.Bucket,
        docker_path: str,
        data_access_url: str,
    ) -> lambda_.DockerImageFunction:
        """Create and return the Lambda function."""
        lambda_role = iam.Role(
            self,
            "IalirtCoverageConstructRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        s3_read_write_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:ListBucket", "s3:GetObject", "s3:PutObject"],
            resources=[
                ialirt_bucket.bucket_arn,
                f"{ialirt_bucket.bucket_arn}/*",
            ],
        )

        # Lambda function
        ialirt_coverage_lambda = lambda_.DockerImageFunction(
            self,
            id="IalirtCoverageLambda",
            code=lambda_.DockerImageCode.from_image_asset(
                docker_path,
                file="IAlirtCode/Dockerfile.coverage",
            ),
            function_name="ialirt-coverage",
            timeout=Duration.minutes(1),
            memory_size=1000,
            role=lambda_role,
            environment={
                "S3_BUCKET": ialirt_bucket.bucket_name,
                "IMAP_DATA_ACCESS_URL": data_access_url,
            },
        )

        ialirt_coverage_lambda.add_to_role_policy(s3_read_write_policy)

        # The resource is deleted when the stack is deleted.
        ialirt_coverage_lambda.apply_removal_policy(RemovalPolicy.DESTROY)

        return ialirt_coverage_lambda

    def create_event_rule(
        self, ialirt_coverage_lambda: lambda_.DockerImageFunction
    ) -> None:
        """Create the event rule to trigger Lambda once per day."""
        # Scheduled rule - daily at 00:00 UTC
        rule = events.Rule(
            self,
            "IalirtDailyCoverageRule",
            schedule=events.Schedule.cron(minute="0", hour="0"),
        )
        rule.add_target(targets.LambdaFunction(ialirt_coverage_lambda))
