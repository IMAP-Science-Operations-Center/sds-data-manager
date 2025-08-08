"""Cron job to create ialirt alarm."""

from aws_cdk import Duration, RemovalPolicy, aws_s3
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

from aws_cdk import Duration, RemovalPolicy, aws_cloudwatch as cw, aws_sns as sns, aws_sns_subscriptions as subs
from aws_cdk import aws_events as events, aws_events_targets as targets
from aws_cdk import aws_iam as iam, aws_lambda as lambda_, aws_s3
from constructs import Construct
import textwrap

from aws_cdk import (
    Stack, Duration,
    aws_s3 as s3,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cloudwatch_actions,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
)


class IalirtAlarmConstruct(Construct):
    """Construct for ialirt coverage."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        ialirt_bucket: aws_s3.Bucket,
        docker_path: str = "sds_data_manager/lambda_code",
        alarm_email: str | None = None,
        **kwargs,
    ) -> None:
        """Create ialirt coverage files.

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

        self.setup_monitoring(ialirt_bucket, alarm_email=alarm_email)

    def setup_monitoring(self, ialirt_bucket):
        """Set up monitoring for unhealthy host count and connection error rate."""
        # Currently sets up an integration with Slack to send notifications
        # to a channel. This is done through an SNS Topic and AWS Chatbot.
        #
        # This had to be done manually in the AWS console to authorize the
        # AWS account to use the Slack app.
        # Just follow the steps here to create a new Slack authorization.
        # https://us-east-2.console.aws.amazon.com/chatbot/home
        # Choosing the channel and permissions as you go through the steps.

        # Create SNS Topic for CloudWatch Alarm
        alarm_topic = sns.Topic(
            self, "IalirtAlarmTopic", display_name="I-ALiRT Alarm Notifications"
        )
        alarm_topic.add_subscription(subs.EmailSubscription("you@example.com"))

        # CloudWatch metric for PutRequests with dimensions
        put_metric = cw.Metric(
            namespace="AWS/S3",
            metric_name="PutRequests",
            period=Duration.minutes(1),
            statistic="Minimum",
            dimensions_map={
                "BucketName": ialirt_bucket.bucket_name,
                "FilterId": "EntireBucket",  # must match the BucketMetrics id above
            },
        )

        # Alarm: “no puts for 15 minutes”
        cloudwatch.Alarm(
            self, "NoPuts15m",
            metric=put_metric,
            threshold=1,  # < 1 put
            # How many periods should it be evaluated before triggering the alarm.
            evaluation_periods=15,  # 15 x 1-minute periods
            datapoints_to_alarm=15,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            alarm_description="Alarm when no packets have arrived.",
        ).add_alarm_action(cloudwatch_actions.SnsAction(alarm_topic))
