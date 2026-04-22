"""Module containing constructs for instrumenting Lambda functions."""

import datetime

from aws_cdk import Duration, Environment
from aws_cdk import aws_ec2 as ec2
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import (
    aws_scheduler as scheduler,
)
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
            # Set max to 15 mins temporarily for the 3 month map cadence job.
            # TODO determine if we need 15 mins or if we can reduce this.
            timeout=Duration.minutes(15),
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
            actions=[
                "events:PutEvents",
                "batch:SubmitJob",
                "batch:DescribeJobDefinitions",
                "ecr:DescribeImages",
            ],
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

        # Add api routes for triggering batch starter with a bulk reprocessing request
        # Only allow authenticated routes for reprocessing
        api.add_route(
            route="/authorized/reprocess",
            http_method="POST",
            lambda_function=self.instrument_lambda,
        )
        api.add_route(
            route="/api-key/reprocess",
            http_method="POST",
            lambda_function=self.instrument_lambda,
        )

        # Set up eventBridge rules to trigger batch starter lambda.
        # create a permission for Scheduled rules
        self.instrument_lambda.add_permission(
            "AllowEventBridgeInvokeScheduled",
            principal=iam.ServicePrincipal("events.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=f"arn:aws:events:{env.region}:{env.account}:rule/ProcessingScheduledJob*",
        )
        # Create IAM role for EventBridge Scheduler
        scheduler_role = iam.Role(
            scope=scope,
            id="SchedulerExecutionRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )

        self.instrument_lambda.grant_invoke(scheduler_role)

        # Many l2 jobs create maps and need 3-12 months worth of data to run.
        # Create eventBridge rules to trigger:
        #    - 3 month map jobs (every 365.25 / 4 days)
        #    - 6 month map jobs (every 365.25 / 2 days)
        #    - 1 year map jobs (every 365.25 days)
        # Note: We are defining the schedules to run at minute level intervals because
        # AWS EventBridge Scheduler does not allow for decimal values in the rate
        # expression. E.g., we cannot specify "rate(91.2 days)" for 3 months.
        # The first trigger date for each map cadence:
        #    - 3 month maps start at FIRST_MAP_START_DATE + 3 months
        #    - 6 month maps start at FIRST_MAP_START_DATE + 6 months
        #    - 1 year maps start at FIRST_MAP_START_DATE + 1 year

        today = datetime.datetime.now(tz=datetime.timezone.utc)
        # loop through dictionary of cadence labels and their corresponding CadenceDays
        # enum objects
        for label, cadence_obj in CadenceDays.str_lookup().items():
            # Calculate interval in minutes
            interval_minutes = int(cadence_obj.value * 24 * 60)
            first_job = cadence_obj.get_first_job_start_date()
            # Calculate the next run time based on the first job date and the cadence
            next_run = calculate_next_run(first_job, today, interval_minutes)
            # Format date as yyyy-MM-ddTHH:mm:ss.SSSZ (with milliseconds)
            start_date_str = next_run.strftime("%Y-%m-%dT%H:%M:%S.000Z")

            # Create a dead letter queue for failed scheduled events
            dlq = sqs.Queue(
                self,
                f"DLQ_{cadence_obj.name.lower()}",
                queue_name=f"ProcessingCadenceJob_{cadence_obj.name.lower()}_failed_jobs_dlq",
            )
            # Grand permissions to allow scheduler to send messages to the DLQ
            # TODO set up an alarm for the DLQ so that we are notified if there are
            #  failed scheduled events
            dlq.grant_send_messages(scheduler_role)
            scheduler.CfnSchedule(
                scope=scope,
                id=f"ProcessingCadenceJob_{cadence_obj.name.lower()}",
                name=f"ProcessingCadenceJob_{cadence_obj.name.lower()}",
                schedule_expression=f"rate({interval_minutes} minutes)",
                # Start the schedule at the next calculated occurrence
                start_date=start_date_str,
                flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                    mode="OFF"
                ),
                target=scheduler.CfnSchedule.TargetProperty(
                    arn=self.instrument_lambda.function_arn,
                    role_arn=scheduler_role.role_arn,
                    dead_letter_config=scheduler.CfnSchedule.DeadLetterConfigProperty(
                        arn=dlq.queue_arn
                    ),
                    input=f'{{"cadence": "{label}"}}',
                    retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                        maximum_retry_attempts=10
                    ),
                ),
                state="ENABLED",
            )


def calculate_next_run(first_job, today, interval_minutes):
    """Calculate the next run time for a scheduled job.

    This function is necessary because AWS EventBridge Scheduler does not support
    starting a schedule at a date in the past. Therefore, we need to calculate
    the next occurrence of the schedule if we are already past the "first job" date.

    Parameters
    ----------
    first_job : datetime.datetime
        The date of the first job.
    today : datetime.datetime
        The current date.
    interval_minutes : int
        The interval in minutes for the cadence.

    Returns
    -------
    datetime.datetime
        The next run date.
    """
    # If today is before the first job, return the first job date as the next run date
    if today < first_job:
        return first_job
    else:
        # Calculate how many minutes have passed since the first job
        delta = (today - first_job).total_seconds() / 60
        # Calculate how many cadence events have passed since the first job
        events_passed = int(delta // interval_minutes)
        # Calculate the next run date
        next_run = first_job + datetime.timedelta(
            minutes=(events_passed + 1) * interval_minutes
        )
        return next_run
