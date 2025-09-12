"""Cron job to create ialirt alarm."""

from typing import Optional

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
from aws_cdk import aws_ssm as ssm
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

        # Parameter store lookup.
        # Note: this must be run once for each account:
        # aws ssm put-parameter --name /imap/ialirt/alarm_email
        # --value ialirt@example.com --type String --overwrite
        ialirt_alarm_email = ssm.StringParameter.value_for_string_parameter(
            self, "/imap/ialirt/alarm_email"
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
            period=Duration.hours(24),  # aggregate per day
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
            evaluation_periods=1,  # single daily datapoint
            datapoints_to_alarm=1,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            alarm_description="Alarm when no packets have arrived in the last day.",
        )
        alarm.add_alarm_action(cloudwatch_actions.SnsAction(alarm_topic))

        return alarm
