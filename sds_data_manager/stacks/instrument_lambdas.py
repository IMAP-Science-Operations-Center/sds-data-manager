"""Module containing constructs for instrumenting Lambda functions."""

from pathlib import Path

from aws_cdk import Duration, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_events as events
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_python_alpha as lambda_alpha
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secrets
from constructs import Construct

from sds_data_manager.stacks.database_stack import SdpDatabase


class BatchStarterLambda(Stack):
    """Generic Construct with customizable runtime code"""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        data_bucket: s3.Bucket,
        code_path: str or Path,
        rds_stack: SdpDatabase,
        rds_security_group: ec2.SecurityGroup,
        subnets: ec2.SubnetSelection,
        vpc: ec2.Vpc,
        **kwargs,
    ):
        """
        BatchStarterLambda Constructor.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        data_bucket: s3.Bucket
            S3 bucket
        code_path : str or Path
            Path to the Lambda code directory
        rds_stack: SdpDatabase
            Database stack
        rds_security_group : ec2.SecurityGroup
            RDS security group
        subnets : ec2.SubnetSelection
            RDS subnet selection.
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        """

        super().__init__(scope, construct_id, **kwargs)

        # Define Lambda Environment Variables
        # TODO: if we need more variables change so we can pass as input
        lambda_environment = {
            "S3_BUCKET": f"{data_bucket.bucket_name}",
            "SECRET_NAME": rds_stack.rds_creds.secret_name,
            "ACCOUNT": f"{self.account}",
            "REGION": f"{self.region}",
        }

        self.instrument_lambda = lambda_alpha.PythonFunction(
            self,
            "BatchStarterLambda",
            function_name="BatchStarterLambda",
            entry=str(Path(__file__).parent.joinpath("..", "lambda_code").resolve()),
            index="SDSCode/batch_starter.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            environment=lambda_environment,
            retry_attempts=0,
            memory_size=512,
            timeout=Duration.minutes(1),
            vpc=vpc,
            vpc_subnets=subnets,
            security_groups=[rds_security_group],
            allow_public_subnet=True,
        )

        data_bucket.grant_read_write(self.instrument_lambda)

        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", rds_stack.secret_name
        )
        rds_secret.grant_read(grantee=self.instrument_lambda)

        # EventBridge Rule for this lambda
        event_from_indexer_lambda = events.Rule(
            self,
            "EventFromIndexerLambda",
            rule_name="event-from-indexer-lambda",
            event_pattern=events.EventPattern(
                source=["imap.lambda"],
                detail_type=["Processed File"],
                detail={
                    "object": {"key": [{"exists": True}]},
                },
            ),
        )

        event_from_indexer_lambda.add_target(
            targets.LambdaFunction(self.instrument_lambda)
        )
