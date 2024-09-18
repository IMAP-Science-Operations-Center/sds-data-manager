"""Configure the ialirt ingest lambda construct."""

import pathlib

import aws_cdk as cdk
from aws_cdk import RemovalPolicy, aws_dynamodb, aws_s3
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_python_alpha as lambda_alpha_
from constructs import Construct


class IalirtIngestLambda(Construct):
    """Construct for ialirt ingest lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        ialirt_bucket: aws_s3.Bucket,
        **kwargs,
    ) -> None:
        """IalirtIngestLambda Stack.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        ialirt_bucket : aws_s3.Bucket
            The data bucket.
        kwargs : dict
            Keyword arguments.

        """
        super().__init__(scope, construct_id, **kwargs)

        # Create DynamoDB Table
        self.packet_data_table = self.create_dynamodb_table()

        # Create Lambda Function
        self.ialirt_ingest_lambda = self.create_lambda_function(
            ialirt_bucket, self.packet_data_table
        )

        # Create Event Rule
        self.create_event_rule(ialirt_bucket, self.ialirt_ingest_lambda)

    def create_dynamodb_table(self) -> aws_dynamodb.Table:
        """Create and return the DynamoDB table."""
        table = ddb.Table(
            self,
            "IalirtPacketDataTable",
            table_name="ialirt-packetdata-table",
            # Change to RemovalPolicy.RETAIN to keep the table after stack deletion.
            # TODO: change to RETAIN in production.
            removal_policy=RemovalPolicy.DESTROY,
            # Restore data to any point in time within the last 35 days.
            # TODO: change to True in production.
            point_in_time_recovery=False,
            # Partition key (PK) = Mission Elapsed Time (MET).
            partition_key=ddb.Attribute(
                name="met",
                type=ddb.AttributeType.NUMBER,
            ),
            # Sort key (SK) = Ingest Time (ISO).
            sort_key=ddb.Attribute(
                name="ingest_time",
                type=ddb.AttributeType.STRING,
            ),
            # Define the read and write capacity units.
            # TODO: change to provisioned capacity mode in production.
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,  # On-Demand capacity mode.
        )
        return table

    def create_lambda_function(
        self, ialirt_bucket: aws_s3.Bucket, packet_data_table: aws_dynamodb.Table
    ) -> lambda_alpha_.PythonFunction:
        """Create and return the Lambda function."""
        lambda_role = iam.Role(
            self,
            "IalirtIngestLambdaRole",
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

        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:GetObject",
                ],
                resources=[
                    packet_data_table.table_arn,
                    f"{ialirt_bucket.bucket_arn}/*",
                ],
            )
        )

        ialirt_ingest_lambda = lambda_alpha_.PythonFunction(
            self,
            id="IalirtIngestLambda",
            function_name="ialirt-ingest",
            entry=str(
                pathlib.Path(__file__).parent.joinpath("..", "lambda_code").resolve()
            ),
            index="IAlirtCode/ialirt_ingest.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            role=lambda_role,
            environment={
                "TABLE_NAME": packet_data_table.table_name,
                "S3_BUCKET": ialirt_bucket.bucket_name,
            },
        )

        packet_data_table.grant_read_write_data(ialirt_ingest_lambda)

        # The resource is deleted when the stack is deleted.
        ialirt_ingest_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        return ialirt_ingest_lambda

    def create_event_rule(
        self,
        ialirt_bucket: aws_s3.Bucket,
        ialirt_ingest_lambda: lambda_alpha_.PythonFunction,
    ) -> None:
        """Create the event rule to trigger Lambda on S3 object creation."""
        ialirt_data_arrival_rule = events.Rule(
            self,
            "IalirtDataArrival",
            rule_name="ialirt-data-arrival",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [ialirt_bucket.bucket_name]},
                    "object": {"key": [{"prefix": "packets/"}]},
                },
            ),
        )

        # Add the Lambda function as the target for the rules
        ialirt_data_arrival_rule.add_target(
            targets.LambdaFunction(ialirt_ingest_lambda)
        )
