"""Cron job to archive ialirt cdf."""

from aws_cdk import Duration, RemovalPolicy, aws_s3
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class IalirtArchiveConstruct(Construct):
    """Construct for ialirt archive."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        algorithm_data_table: ddb.Table,
        ialirt_bucket: aws_s3.Bucket,
        docker_path: str = "sds_data_manager/lambda_code",
        **kwargs,
    ) -> None:
        """Create ialirt cdf.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        algorithm_data_table : ddb.Table
            Algorithm database table.
        ialirt_bucket : aws_s3.Bucket
            The data bucket.
        docker_path : str
            Path to the Dockerfile.
        kwargs : dict
            Keyword arguments.

        """
        super().__init__(scope, construct_id, **kwargs)

        # Create Lambda Function
        ialirt_archive_lambda = self.create_archive_lambda(
            ialirt_bucket, algorithm_data_table, docker_path
        )
        self.create_event_rule(ialirt_archive_lambda)

    def create_archive_lambda(
        self,
        ialirt_bucket: aws_s3.Bucket,
        algorithm_data_table: ddb.Table,
        docker_path: str,
    ) -> lambda_.DockerImageFunction:
        """Create and return the Lambda function."""
        lambda_role = iam.Role(
            self,
            "IalirtArchiveConstructRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonDynamoDBFullAccess"
                ),
            ],
        )

        # Lambda function
        ialirt_archive_lambda = lambda_.DockerImageFunction(
            self,
            id="IalirtArchiveLambda",
            code=lambda_.DockerImageCode.from_image_asset(
                docker_path,
                file="IAlirtCode/Dockerfile.archive",
            ),
            function_name="ialirt-archive",
            timeout=Duration.minutes(1),
            memory_size=1000,
            role=lambda_role,
            environment={
                "ALGORITHM_TABLE": algorithm_data_table.table_name,
                "S3_BUCKET": ialirt_bucket.bucket_name,
            },
        )

        algorithm_data_table.grant_read_write_data(ialirt_archive_lambda)
        ialirt_bucket.grant_put(ialirt_archive_lambda)

        # The resource is deleted when the stack is deleted.
        ialirt_archive_lambda.apply_removal_policy(RemovalPolicy.DESTROY)

        return ialirt_archive_lambda

    def create_event_rule(
        self, ialirt_archive_lambda: lambda_.DockerImageFunction
    ) -> None:
        """Create the event rule to trigger Lambda on S3 object creation."""
        # Scheduled rule - daily at 00:00 UTC
        rule = events.Rule(
            self,
            "IalirtDailyQueryRule",
            schedule=events.Schedule.cron(minute="0", hour="0"),
        )
        rule.add_target(targets.LambdaFunction(ialirt_archive_lambda))
