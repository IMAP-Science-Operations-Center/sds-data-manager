"""Cron job to create ialirt realtime files."""

from aws_cdk import Duration, RemovalPolicy, aws_s3
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class IalirtRealTimeConstruct(Construct):
    """Construct for ialirt realtime plots."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        ialirt_bucket: aws_s3.Bucket,
        docker_path: str = "sds_data_manager/lambda_code",
        **kwargs,
    ) -> None:
        """Create ialirt realtime files.

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
        ialirt_realtime_lambda = self.create_realtime_lambda(ialirt_bucket, docker_path)
        # Create Event Rule
        self.create_event_rule(ialirt_bucket, ialirt_realtime_lambda)

    def create_realtime_lambda(
        self,
        ialirt_bucket: aws_s3.Bucket,
        docker_path: str,
    ) -> lambda_.DockerImageFunction:
        """Create and return the Lambda function."""
        lambda_role = iam.Role(
            self,
            "IalirtRealTimeConstructRole",
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
        ialirt_realtime_lambda = lambda_.DockerImageFunction(
            self,
            id="IalirtRealTimeLambda",
            code=lambda_.DockerImageCode.from_image_asset(
                docker_path,
                file="IAlirtCode/Dockerfile.realtime",
            ),
            function_name="ialirt-realtime",
            timeout=Duration.minutes(1),
            memory_size=1000,
            role=lambda_role,
            environment={
                "S3_BUCKET": ialirt_bucket.bucket_name,
            },
        )

        ialirt_realtime_lambda.add_to_role_policy(s3_read_write_policy)

        # The resource is deleted when the stack is deleted.
        ialirt_realtime_lambda.apply_removal_policy(RemovalPolicy.DESTROY)

        return ialirt_realtime_lambda

    def create_event_rule(
        self,
        ialirt_bucket: aws_s3.Bucket,
        ialirt_realtime_lambda: lambda_.DockerImageFunction,
    ) -> None:
        """Create the event rule to trigger Lambda on S3 object creation."""
        ialirt_log_arrival_rule = events.Rule(
            self,
            "IalirtLogArrival",
            rule_name="ialirt-log-arrival",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [ialirt_bucket.bucket_name]},
                    "object": {"key": [{"prefix": "logs/"}]},
                },
            ),
        )

        # Add the Lambda function as the target for the rules
        ialirt_log_arrival_rule.add_target(
            targets.LambdaFunction(ialirt_realtime_lambda)
        )
