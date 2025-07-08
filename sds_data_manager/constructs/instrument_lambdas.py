"""Module containing constructs for instrumenting Lambda functions."""

import datetime

import aws_cdk as cdk
from aws_cdk import Duration, Environment, aws_events
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secrets
from aws_cdk import aws_sqs as sqs
from aws_cdk.aws_lambda_event_sources import SqsEventSource
from constructs import Construct

from sds_data_manager.constructs.api_gateway_construct import ApiGateway
from sds_data_manager.constructs.database_construct import SdpDatabase
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter import (
    CadenceDays,
)


class BatchStarterLambda(Construct):
    """Generic Construct with customizable runtime code."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env: Environment,
        api: ApiGateway,
        data_bucket: s3.Bucket,
        code: lambda_.Code,
        rds_construct: SdpDatabase,
        rds_security_group: ec2.SecurityGroup,
        vpc: ec2.Vpc,
        sqs_queues: list[sqs.Queue],
        layers: list,
        **kwargs,
    ):
        """BatchStarterLambda Constructor.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        env : Environment
            Account and region.
        api : obj
            The APIGateway stack
        data_bucket: s3.Bucket
            S3 bucket.
        code : lambda_.Code
            Lambda code bundle.
        rds_construct: SdpDatabase
            Database stack.
        rds_security_group : ec2.SecurityGroup
            RDS security group.
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        sqs_queues: list[sqs.Queue]
            A FIFO queue to trigger the lambda with.
        layers : list
            List of Lambda layers cdk.cdfnOutput names.
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        # Define Lambda Environment Variables
        lambda_environment = {
            "S3_BUCKET": f"{data_bucket.bucket_name}",
            "SECRET_NAME": rds_construct.rds_creds.secret_name,
            "ACCOUNT": f"{env.account}",
            "REGION": f"{env.region}",
        }
        # Lambda should use private subnet
        subnet = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)

        self.instrument_lambda = lambda_.Function(
            self,
            "BatchStarterLambda",
            function_name="BatchStarterLambda",
            code=code,
            handler="SDSCode.pipeline_lambdas.batch_starter.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            environment=lambda_environment,
            memory_size=512,
            timeout=Duration.minutes(1),
            vpc=vpc,
            vpc_subnets=subnet,
            security_groups=[rds_security_group],
            allow_public_subnet=True,
            layers=layers,
        )

        # Permissions to send event to EventBridge
        # and submit batch job
        lambda_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["events:PutEvents", "batch:SubmitJob"],
            resources=[
                "*",
            ],
        )
        self.instrument_lambda.add_to_role_policy(lambda_policy)
        data_bucket.grant_read_write(self.instrument_lambda)
        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", rds_construct.secret_name
        )
        rds_secret.grant_read(grantee=self.instrument_lambda)

        # This sets up the lambda to be triggered by the SQS queues. Since they are FIFO
        # queues, each instrument will have messages processed in order. However,
        # different instruments will be processed in parallel, with multiple instances
        # of the batch_starter lambda.
        # The nominal case is for there to be a file arrived queue and a delayed
        # file arrived queue. On the batch starter side, all events will look the
        # same from the two queues.
        for q in sqs_queues:
            self.instrument_lambda.add_event_source(SqsEventSource(q))

        # Add api route for triggering batch starter with a bulk reprocessing request
        api.add_route(
            route="/reprocess",
            http_method="POST",
            lambda_function=self.instrument_lambda,
        )
        api.add_route(
            route="/authorized/reprocess",
            http_method="POST",
            lambda_function=self.instrument_lambda,
        )

        # Set up eventBridge rules to trigger batch starter lambda.
        # create one permission for all eventbridge rules
        self.instrument_lambda.add_permission(
            "AllowEventBridgeInvoke",
            principal=iam.ServicePrincipal("events.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=f"arn:aws:events:{env.region}:{env.account}:rule/ProcessingCadenceJob*",
        )
        # Many l2 jobs create maps and need 3-12 months worth of data to run.
        # Create eventBridge rules to trigger:
        #    - 3 month map jobs (every 365.25 / 4 days)
        #    - 6 month map jobs (every 365.25 / 2 days)
        #    - 1 year map jobs (every 365.25 days)
        # Note: each map job trigger will have its own eventBridge rule, because we need
        # them to run every x days starting from the same date (t0).
        # TODO what would be a good start date?
        first_job = datetime.datetime(2026, 1, 1)
        t0_date = first_job - datetime.timedelta(days=CadenceDays.THREE_MONTHS)
        today = datetime.datetime.now()
        # Create rules for 15 years (far beyond what we expect as a precaution)
        # AWS event bridge supports up to 500 rules, so this is well within the limit.
        total_days = CadenceDays.ONE_YEAR * 15
        for i in range(1, int(total_days // CadenceDays.THREE_MONTHS.value)):
            date = t0_date + datetime.timedelta(days=CadenceDays.THREE_MONTHS.value * i)
            if date < today:
                # Skip dates that are in the past. This might be the case if we are
                # deploying this construct after the t0_date.
                continue
            string_date = date.strftime("%Y%m%d")
            cron_exp = (
                f"cron({date.minute} {date.hour} {date.day} {date.month} ? {date.year})"
            )
            # TODO retry count is set to 185???
            aws_events.CfnRule(
                scope=scope,
                id=f"ProcessingCadenceJob3month_{string_date}",
                name=f"ProcessingCadenceJob3month_{string_date}",
                description=f"Trigger 'batch starter' processing job on {string_date}",
                schedule_expression=cron_exp,
                state="ENABLED",
                targets=[
                    aws_events.CfnRule.TargetProperty(
                        arn=self.instrument_lambda.function_arn,
                        id=f"Target{string_date}",
                        input=cdk.Fn.sub('{"cadence": "3mo"}'),
                    )
                ],
            )
        for i in range(1, int(total_days // CadenceDays.SIX_MONTHS.value)):
            date = t0_date + datetime.timedelta(days=CadenceDays.SIX_MONTHS.value * i)
            string_date = date.strftime("%Y%m%d")
            if date < today:
                continue
            cron_exp = (
                f"cron({date.minute} {date.hour} {date.day} {date.month} ? {date.year})"
            )
            aws_events.CfnRule(
                scope=scope,
                id=f"ProcessingCadenceJob6month_{string_date}",
                name=f"ProcessingCadenceJob6month_{string_date}",
                description=f"Trigger 'batch starter' processing job on {string_date}",
                schedule_expression=cron_exp,
                state="ENABLED",
                targets=[
                    aws_events.CfnRule.TargetProperty(
                        arn=self.instrument_lambda.function_arn,
                        id=f"Target{string_date}",
                        input=cdk.Fn.sub('{"cadence": "6mo"}'),
                    )
                ],
            )
        for i in range(1, int(total_days // CadenceDays.ONE_YEAR.value)):
            date = t0_date + datetime.timedelta(days=CadenceDays.ONE_YEAR.value * i)
            string_date = date.strftime("%Y%m%d")
            if date < today:
                continue
            cron_exp = (
                f"cron({date.minute} {date.hour} {date.day} {date.month} ? {date.year})"
            )
            aws_events.CfnRule(
                scope=scope,
                id=f"ProcessingCadenceJob1year_{string_date}",
                name=f"ProcessingCadenceJob1year_{string_date}",
                description=f"Trigger 'batch starter' processing job on {string_date}",
                schedule_expression=cron_exp,
                state="ENABLED",
                targets=[
                    aws_events.CfnRule.TargetProperty(
                        arn=self.instrument_lambda.function_arn,
                        id=f"Target{string_date}",
                        input=cdk.Fn.sub('{"cadence": "1yr"}'),
                    )
                ],
            )
