"""Cron job to create ialirt alarm."""

from typing import Optional

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    aws_s3,
)
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
)
from aws_cdk import (
    aws_cloudwatch_actions as cloudwatch_actions,
)
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
        self.setup_monitoring(ialirt_bucket, ialirt_alarm_email)

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
        cloudwatch.Alarm(
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
        ).add_alarm_action(cloudwatch_actions.SnsAction(alarm_topic))
