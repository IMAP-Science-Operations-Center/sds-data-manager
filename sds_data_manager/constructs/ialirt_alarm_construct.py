"""Cron job to create ialirt alarm."""

from typing import Optional

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    aws_s3,
)
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
)
from aws_cdk import (
    aws_cloudwatch_actions as cloudwatch_actions,
)
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import (
    aws_sns as sns,
)
from aws_cdk import (
    aws_sns_subscriptions as subs,
)
from constructs import Construct


class IalirtAlarmConstruct(Construct):
    """Construct for ialirt alarm."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        ialirt_bucket: aws_s3.Bucket,
        code: lambda_.Code,
        **kwargs,
    ) -> None:
        """Create ialirt alarm.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        ialirt_bucket : aws_s3.Bucket
            The data bucket to monitor.
        code : lambda_.Code
            Lambda code bundle.
        kwargs : dict
            Keyword arguments.

        """
        super().__init__(scope, construct_id, **kwargs)

        # Upon deployment we must do
        # cdk deploy -c alarm_email=ops@example.com
        ialirt_alarm_email = self.node.try_get_context("alarm_email")
        if not (isinstance(ialirt_alarm_email, str) and "@" in ialirt_alarm_email):
            ialirt_alarm_email = None
            cdk.Annotations.of(self).add_warning(
                "No alarm_email provided. Set one with: "
                "cdk deploy -c alarm_email=ops@example.com"
            )
        alarm = self.setup_monitoring(ialirt_bucket, ialirt_alarm_email)
        self.create_reset_alarm_lambda(code, alarm.alarm_name)

    def setup_monitoring(self, ialirt_bucket, ialirt_alarm_email: Optional[str]):
        """Create SNS topic for CloudWatch alarm."""
        alarm_topic = sns.Topic(
            self, "IalirtAlarmTopic", display_name="I-ALiRT Alarm Notifications"
        )
        if ialirt_alarm_email:
            alarm_topic.add_subscription(subs.EmailSubscription(ialirt_alarm_email))

        # CloudWatch metric for PutRequests with dimensions
        put_metric = cloudwatch.Metric(
            namespace="AWS/S3",
            metric_name="PutRequests",
            period=Duration.minutes(1),  # Check every minute
            statistic="Sum",
            dimensions_map={
                "BucketName": ialirt_bucket.bucket_name,
                "FilterId": "PacketsPrefix",
            },
        )

        # Alarm: “no puts for 1 day”
        alarm = cloudwatch.Alarm(
            self,
            "IalirtNoPutsDay",
            metric=put_metric,
            threshold=1,  # < 1 put
            # How many periods should it be evaluated before triggering the alarm.
            evaluation_periods=1440,  # 1 day total window
            datapoints_to_alarm=1440,  # all must be quiet
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            alarm_description="Alarm when no packets have arrived.",
        )
        alarm.add_alarm_action(cloudwatch_actions.SnsAction(alarm_topic))

        return alarm

    def create_reset_alarm_lambda(self, code, alarm_name):
        """Create a Lambda that resets the alarm daily using existing code."""
        reset_lambda = lambda_.Function(
            self,
            "IalirtResetAlarmLambda",
            function_name="ResetAlarmLambda",
            code=code,
            handler="IAlirtCode.ialirt_alarm.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            memory_size=512,
            timeout=Duration.seconds(30),
            environment={"ALARM_NAME": alarm_name},
        )

        lambda_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["cloudwatch:SetAlarmState"],
            resources=["*"],
        )

        reset_lambda.add_to_role_policy(lambda_policy)
        # Delete the Lambda when stack is deleted.
        reset_lambda.apply_removal_policy(RemovalPolicy.DESTROY)

        # CloudWatch Event Rule (daily at 00:01 UTC)
        # Reset alarm at this time.
        rule = events.Rule(
            self,
            "IalirtResetAlarmSchedule",
            schedule=events.Schedule.expression("cron(1 0 * * ? *)"),
        )
        rule.add_target(targets.LambdaFunction(reset_lambda))

        return reset_lambda
