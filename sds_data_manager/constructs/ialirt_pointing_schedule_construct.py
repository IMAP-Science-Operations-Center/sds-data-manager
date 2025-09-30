"""Cron job to created pointing schedule."""

from aws_cdk import Duration, RemovalPolicy, aws_s3
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class IalirtPointingConstruct(Construct):
    """Construct for ialirt pointing schedule."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        ialirt_bucket: aws_s3.Bucket,
        docker_path: str = "sds_data_manager/lambda_code",
        **kwargs,
    ) -> None:
        """Create ialirt pointing schedule.

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
        kwargs : dict
            Keyword arguments.

        """
        super().__init__(scope, construct_id, **kwargs)

        # Create Lambda Function
        ialirt_pointing_lambda = self.create_pointing_lambda(ialirt_bucket, docker_path)
        self.create_event_rule(ialirt_pointing_lambda)

    def create_pointing_lambda(
        self,
        ialirt_bucket: aws_s3.Bucket,
        docker_path: str,
    ) -> lambda_.DockerImageFunction:
        """Create and return the Lambda function."""
        lambda_role = iam.Role(
            self,
            "IalirtPointingConstructRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # Lambda function
        ialirt_pointing_lambda = lambda_.DockerImageFunction(
            self,
            id="IalirtPointingLambda",
            code=lambda_.DockerImageCode.from_image_asset(
                docker_path,
                file="IAlirtCode/Dockerfile.pointing",
            ),
            function_name="ialirt-pointing-schedule",
            timeout=Duration.minutes(1),
            memory_size=1000,
            role=lambda_role,
            environment={
                "S3_BUCKET": ialirt_bucket.bucket_name,
            },
        )

        ialirt_bucket.grant_put(ialirt_pointing_lambda)

        # The resource is deleted when the stack is deleted.
        ialirt_pointing_lambda.apply_removal_policy(RemovalPolicy.DESTROY)

        return ialirt_pointing_lambda

    def create_event_rule(
        self, ialirt_pointing_lambda: lambda_.DockerImageFunction
    ) -> None:
        """Create the event rule to trigger Lambda once per day."""
        # Scheduled rule - daily at 00:00 UTC
        rule = events.Rule(
            self,
            "IalirtDailyPointingRule",
            schedule=events.Schedule.cron(minute="0", hour="0"),
        )
        rule.add_target(targets.LambdaFunction(ialirt_pointing_lambda))
