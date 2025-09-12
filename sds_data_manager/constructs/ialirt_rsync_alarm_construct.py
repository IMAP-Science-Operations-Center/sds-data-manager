"""Cron job to alarm on rsync failure."""

from aws_cdk import Duration, RemovalPolicy, aws_s3
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct
from aws_cdk import aws_ssm as ssm

from aws_cdk import (
    aws_cloudwatch as cloudwatch,
)
from aws_cdk import (
    aws_cloudwatch_actions as cloudwatch_actions,
)
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs

from typing import Optional


class IalirtRsyncAlarmConstruct(Construct):
    """Construct for ialirt realtime plots."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        code: lambda_.Code,
        ialirt_bucket: aws_s3.Bucket,
        **kwargs,
    ) -> None:
        """Create ialirt realtime files.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        code : lambda_.Code
            Lambda code bundle
        ialirt_bucket : aws_s3.Bucket
            The data bucket.
        kwargs : dict
            Keyword arguments.

        """
        super().__init__(scope, construct_id, **kwargs)

        # Create Lambda Function
        ialirt_rsync_lambda = self.create_realtime_lambda(ialirt_bucket, code)
        # Create Event Rule
        self.create_event_rule(ialirt_bucket, ialirt_rsync_lambda)
        # Parameter store lookup.
        # Note: this must be run once for each account:
        # aws ssm put-parameter --name /imap/ialirt/alarm_email
        # --value ialirt@example.com --type String --overwrite
        rsync_alarm_email = ssm.StringParameter.value_for_string_parameter(
            self, "/imap/ialirt/rsync_alarm_email"
        )
        self.setup_monitoring(rsync_alarm_email)

    def create_realtime_lambda(
        self,
        ialirt_bucket: aws_s3.Bucket,
        code: lambda_.Code,
    ) -> lambda_.Function:
        """Create and return the Lambda function."""
        lambda_role = iam.Role(
            self,
            "IalirtRsyncAlarmConstructRole",
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
        ialirt_rsync_lambda = lambda_.Function(
            self,
            id="IalirtRsyncAlarmLambda",
            function_name="ialirt-rsync-alarm",
            code=code,
            handler="IAlirtCode.ialirt_rsync_alarm.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=Duration.minutes(1),
            memory_size=1000,
            role=lambda_role,
            environment={
                "S3_BUCKET": ialirt_bucket.bucket_name,
            },
        )

        ialirt_rsync_lambda.add_to_role_policy(s3_read_write_policy)

        # The resource is deleted when the stack is deleted.
        ialirt_rsync_lambda.apply_removal_policy(RemovalPolicy.DESTROY)

        return ialirt_rsync_lambda

    def create_event_rule(
        self,
        ialirt_bucket: aws_s3.Bucket,
        ialirt_rsync_lambda: lambda_.Function,
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
        ialirt_log_arrival_rule.add_target(targets.LambdaFunction(ialirt_rsync_lambda))

    def setup_monitoring(self, alarm_email: str):
        """Create CloudWatch alarm and notify via SNS if rsync failures occur."""

        alarm_topic = sns.Topic(
            self,
            "IalirtRsyncAlarmTopic",
            display_name="I-ALiRT Rsync Failure Alarm Notifications",
        )
        alarm_topic.add_subscription(subs.EmailSubscription(alarm_email))

        # Metric for rsync failures (emitted from Lambda)
        rsync_metric = cloudwatch.Metric(
            namespace="IMAP/Ialirt",
            metric_name="IalirtRsyncFailures",
            period=Duration.minutes(5),
            statistic="Sum",
            dimensions_map={"Function": "ialirt-rsync-alarm"},
        )

        alarm = cloudwatch.Alarm(
            self,
            "IalirtRsyncFailureAlarm",
            metric=rsync_metric,
            threshold=1,  # >=1 means a failure happened
            evaluation_periods=1,  # one 5-minute period
            datapoints_to_alarm=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Alarm if rsync failure is detected in Lambda output.",
        )

        alarm.add_alarm_action(cloudwatch_actions.SnsAction(alarm_topic))

        return alarm
