# Standard
import pathlib

# Installed
import aws_cdk as cdk
from aws_cdk import (
    Stack,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import aws_events as events
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_lambda_python_alpha as lambda_alpha_,
)
from aws_cdk import (
    aws_secretsmanager as secrets,
)
from constructs import Construct


class IndexerLambda(Stack):
    """Stack for indexer lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env: cdk.Environment,
        db_secret_name: str,
        vpc: ec2.Vpc,
        vpc_subnets,
        rds_security_group,
        data_bucket,
        **kwargs,
    ) -> None:
        """IndexerLambda Stack

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        indexer_lambda = lambda_alpha_.PythonFunction(
            self,
            id="IndexerLambda",
            function_name="file-indexer",
            entry=str(
                pathlib.Path(__file__).parent.joinpath("..", "lambda_code").resolve()
            ),
            index="SDSCode/indexer.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=cdk.Duration.minutes(15),
            memory_size=1000,
            allow_public_subnet=True,
            vpc=vpc,
            vpc_subnets=vpc_subnets,
            security_groups=[rds_security_group],
            environment={
                "DATA_TRACKER_INDEX": "data_tracker",
                "S3_DATA_BUCKET": data_bucket.s3_url_for_object(),
                "SECRET_NAME": db_secret_name,
            },
        )

        indexer_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", db_secret_name
        )
        rds_secret.grant_read(grantee=indexer_lambda)

        # Instrument list
        instruments = [
            "codice",
            "glows",
            "hi",
            "hit",
            "idex",
            "lo",
            "mag",
            "swapi",
            "swe",
            "ultra",
        ]

        # Events that triggers Indexer Lambda:
        # 1. Arrival of L0 data
        # 2. PutEvent from Lambda that builds dependency and starts Batch Job
        # 3. Batch Job status change

        # Write l0 info to db with
        # status SUCCEEDED
        l0_arrival_rule = events.Rule(
            self,
            "l0DataArrival",
            rule_name="l0-data-arrival",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [data_bucket.bucket_name]},
                    "object": {
                        "key": [
                            {"prefix": f"imap/{instrument}/l0/"}
                            for instrument in instruments
                        ]
                    },
                },
            ),
        )

        # This event listens for PutEvent from Lambda that builds
        # dependency and starts Batch Job.
        # Indexer Lambda listens for that event and writes
        # all information to database
        # and intialized other information such as
        # ingestion time to null.
        # PutEvent Example:
        #     {
        #     "DetailType": "Job Started",
        #     "Source": "aws.lambda",
        #     "Detail": {
        #     "file_to_create": "str",
        #     "status": "InProgress",
        #     "depedency": {    "codice": "s3-filepath", "mag": "s3-filepath"}
        #     }}
        batch_starter_event_rule = events.Rule(
            self,
            "batchStarterEvent",
            rule_name="batch-starter-event",
            event_pattern=events.EventPattern(
                source=["imap.lambda"],
                detail_type=["Batch Job Started"],
                detail={
                    "file_to_create": [{"exists": True}],
                    "status": ["InProgress"],
                    "depedency": [{"exists": True}],
                },
            ),
        )

        # Uses batch job status
        # to update status in the database and
        # update ingested time if status was success
        batch_job_status_rule = events.Rule(
            self,
            "batchJobStatus",
            rule_name="batch-job-status",
            event_pattern=events.EventPattern(
                source=["aws.batch"],
                detail_type=["Batch Job State Change"],
                detail={"status": ["SUCCEEDED", "FAILED"]},
            ),
        )

        # Add the Lambda function as the target for the rules
        l0_arrival_rule.add_target(targets.LambdaFunction(indexer_lambda))
        batch_starter_event_rule.add_target(targets.LambdaFunction(indexer_lambda))
        batch_job_status_rule.add_target(targets.LambdaFunction(indexer_lambda))
