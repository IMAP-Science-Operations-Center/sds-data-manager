"""Cron job to alarm on rsync failure."""

from aws_cdk import Duration, RemovalPolicy, aws_s3
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_ssm as ssm
from constructs import Construct


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

        # Parameter store lookup.
        # Note: this must be run once for each account:
        # aws ssm put-parameter --name /imap/ialirt/alarm_email
        # --value ialirt@example.com --type String --overwrite
        alarm_email = ssm.StringParameter.value_for_string_parameter(
            self, "/imap/ialirt/alarm_email"
        )

        # SNS topic for direct notifications
        alarm_topic = sns.Topic(
            self,
            "IalirtRsyncAlarmTopic",
            display_name="I-ALiRT Rsync Failure Notifications",
        )
        alarm_topic.add_subscription(subs.EmailSubscription(alarm_email))

        ialirt_rsync_lambda = self.create_rsync_lambda(ialirt_bucket, code, alarm_topic)

        self.create_event_rule(ialirt_bucket, ialirt_rsync_lambda)

    def create_rsync_lambda(
        self,
        ialirt_bucket: aws_s3.Bucket,
        code: lambda_.Code,
        alarm_topic: sns.Topic,
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
                "SNS_TOPIC_ARN": alarm_topic.topic_arn,
            },
        )

        ialirt_rsync_lambda.apply_removal_policy(RemovalPolicy.DESTROY)

        return ialirt_rsync_lambda
