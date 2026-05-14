"""Cron job to create ialirt alarm."""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    aws_dynamodb,
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
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
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
        code: lambda_.Code,
        ialirt_bucket: aws_s3.Bucket,
        data_table: aws_dynamodb.Table,
        **kwargs,
    ) -> None:
        """Create ialirt alarms and rsync failure notifications.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        code : lambda_.Code
            Lambda code bundle.
        ialirt_bucket : aws_s3.Bucket
            The data bucket to monitor.
        data_table : aws_dynamodb.Table
            The ialirt-data-table to monitor for instrument freshness.
        kwargs : dict
            Keyword arguments.

        """
        super().__init__(scope, construct_id, **kwargs)

        # Parameter store lookup.
        # Note: this must be run once for each account:
        # aws ssm put-parameter --name /imap/ialirt/alarm_email
        # --value ialirt@example.com --type String --overwrite
        # Setup a notification for no packets arriving.
        ialirt_alarm_email = ssm.StringParameter.value_for_string_parameter(
            self, "/imap/ialirt/alarm_email"
        )
        operation_topic = sns.Topic(
            self,
            "IalirtAlarmTopics",
            display_name="I-ALiRT Operations Alarm Notifications",
        )
        if ialirt_alarm_email:
            operation_topic.add_subscription(subs.EmailSubscription(ialirt_alarm_email))

        # Create CloudWatch monitoring for 'no packets arrived' condition.
        self.setup_monitoring(ialirt_bucket, operation_topic)

        # Setup a notification for rsync failures.
        ops_alarm_email = ssm.StringParameter.value_for_string_parameter(
            self, "/imap/ialirt/ops_alarm_email"
        )
        rsync_topic = sns.Topic(
            self, "IalirtRsyncAlarmTopic", display_name="I-ALiRT Rsync Failure Alarm"
        )
        if ops_alarm_email:
            rsync_topic.add_subscription(subs.EmailSubscription(ops_alarm_email))

        # Create rsync Lambda + event trigger
        self.create_rsync_lambda(ialirt_bucket, code, rsync_topic)

        # Create instrument freshness alarm Lambda + daily schedule
        self.create_instrument_alarm_lambda(code, data_table, operation_topic)

    def create_rsync_lambda(
        self,
        ialirt_bucket: aws_s3.Bucket,
        code: lambda_.Code,
        alarm_topic: sns.Topic,
    ) -> lambda_.Function:
        """Create and return the Lambda function for rsync failure detection."""
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

        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sns:Publish"],
                resources=[alarm_topic.topic_arn],
            )
        )

        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket", "s3:GetObject"],
                resources=[
                    ialirt_bucket.bucket_arn,
                    f"{ialirt_bucket.bucket_arn}/*",
                ],
            )
        )

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
                "SNS_TOPIC_ARN": alarm_topic.topic_arn,
            },
        )

        ialirt_rsync_lambda.apply_removal_policy(RemovalPolicy.DESTROY)

        # Event rule to trigger Lambda on S3 object creation (logs)
        ialirt_log_arrival_rule = events.Rule(
            self,
            "IalirtLogTrigger",
            rule_name="ialirt-log-trigger",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [ialirt_bucket.bucket_name]},
                    "object": {"key": [{"prefix": "logs/"}]},
                },
            ),
        )

        ialirt_log_arrival_rule.add_target(targets.LambdaFunction(ialirt_rsync_lambda))

        return ialirt_rsync_lambda

    def setup_monitoring(self, ialirt_bucket, alarm_topic: sns.Topic):
        """Create SNS topic for CloudWatch alarm."""
        # CloudWatch metric for PutRequests with dimensions
        put_metric = cloudwatch.Metric(
            namespace="AWS/S3",
            metric_name="PutRequests",
            period=Duration.days(1),  # Check every day
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
            evaluation_periods=1,  # 1 day total window
            datapoints_to_alarm=1,  # all must be quiet
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            alarm_description="Alarm when no packets have arrived.",
        )
        alarm.add_alarm_action(cloudwatch_actions.SnsAction(alarm_topic))

        return alarm

    def create_instrument_alarm_lambda(
        self,
        code: lambda_.Code,
        data_table: aws_dynamodb.Table,
        alarm_topic: sns.Topic,
    ) -> lambda_.Function:
        """Create a scheduled Lambda that alerts when an instrument has no recent data.

        Parameters
        ----------
        code : lambda_.Code
            Lambda code bundle.
        data_table : aws_dynamodb.Table
            The ialirt-data-table to query for instrument freshness.
        alarm_topic : sns.Topic
            SNS topic to publish alerts to.

        Returns
        -------
        lambda_.Function
            The created Lambda function.

        """
        lambda_role = iam.Role(
            self,
            "IalirtInstrumentAlarmRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sns:Publish"],
                resources=[alarm_topic.topic_arn],
            )
        )

        data_table.grant_read_data(lambda_role)

        instrument_alarm_lambda = lambda_.Function(
            self,
            id="IalirtInstrumentAlarmLambda",
            function_name="ialirt-instrument-alarm",
            code=code,
            handler="IAlirtCode.ialirt_instrument_alarm.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=Duration.minutes(1),
            memory_size=256,
            role=lambda_role,
            environment={
                "DATA_TABLE": data_table.table_name,
                "SNS_TOPIC_ARN": alarm_topic.topic_arn,
            },
        )

        instrument_alarm_lambda.apply_removal_policy(RemovalPolicy.DESTROY)

        # Run at 11:00 UTC daily.
        daily_rule = events.Rule(
            self,
            "IalirtInstrumentAlarmSchedule",
            rule_name="ialirt-instrument-alarm-schedule",
            schedule=events.Schedule.cron(hour="11", minute="0"),
        )
        daily_rule.add_target(targets.LambdaFunction(instrument_alarm_lambda))

        return instrument_alarm_lambda
