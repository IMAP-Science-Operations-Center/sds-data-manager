"""Configure the indexer lambda."""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secrets
from constructs import Construct


class IndexerLambda(Construct):
    """Construct for indexer lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        code: lambda_.Code,
        db_secret_name: str,
        vpc: ec2.Vpc,
        vpc_subnets,
        rds_security_group,
        data_bucket,
        sns_topic,
        layers: list,
        **kwargs,
    ) -> None:
        """IndexerLambda Construct.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        code : aws_lambda.Code
            Lambda code bundle
        db_secret_name : str
            The DB secret name
        vpc : obj
            The VPC
        vpc_subnets : obj
            The VPC subnets
        rds_security_group : obj
            The RDS security group
        data_bucket : obj
            The data bucket
        sns_topic : aws_sns.Topic
            SNS Topic for sending notifications so that external
            resources can subscribe to for alerts.
        layers : list
            List of Lambda layers cdk.cdfnOutput names
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        indexer_lambda = lambda_.Function(
            self,
            id="IndexerLambda",
            function_name="file-indexer",
            code=code,
            handler="SDSCode.indexer.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            allow_public_subnet=True,
            vpc=vpc,
            vpc_subnets=vpc_subnets,
            security_groups=[rds_security_group],
            environment={
                "DATA_TRACKER_INDEX": "data_tracker",
                "S3_BUCKET": data_bucket.bucket_name,
                "SECRET_NAME": db_secret_name,
            },
            layers=layers,
            architecture=lambda_.Architecture.ARM_64,
        )

        # Adding events and s3 permission because indexer
        # lambda sents events and read from s3.
        # TODO: narrow s3 permission later
        put_event_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["events:PutEvents", "s3:*"],
            resources=[
                "*",
            ],
        )

        indexer_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
        indexer_lambda.add_to_role_policy(put_event_policy)

        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", db_secret_name
        )
        rds_secret.grant_read(grantee=indexer_lambda)

        # Events that triggers Indexer Lambda:
        # 1. Arrival of all science data
        # 2. PutEvent from Lambda that builds dependency and starts Batch Job
        # 3. Batch Job status change

        # Write science data info to db with
        # status SUCCEEDED
        imap_data_arrival_rule = events.Rule(
            self,
            "ImapDataArrival",
            rule_name="imap-data-arrival",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [data_bucket.bucket_name]},
                    "object": {"key": [{"prefix": "imap/"}]},
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

        # Uses batch job status of failure
        # to trigger a sns topic
        batch_job_failure_rule = events.Rule(
            self,
            "batchJobFailure",
            rule_name="batch-job-failure",
            event_pattern=events.EventPattern(
                source=["aws.batch"],
                detail_type=["Batch Job State Change"],
                detail={"status": ["FAILED"]},
            ),
        )

        # Add the Lambda function as the target for the rules
        imap_data_arrival_rule.add_target(targets.LambdaFunction(indexer_lambda))
        batch_job_status_rule.add_target(targets.LambdaFunction(indexer_lambda))
        batch_job_failure_rule.add_target(targets.SnsTopic(sns_topic))
