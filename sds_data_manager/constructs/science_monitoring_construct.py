"""Configure Science data freshness monitoring.

This construct creates a Lambda function that runs daily to check for
stale science data in the database, publishes CloudWatch metrics,
and creates alarms that trigger SNS notifications when data is missing.
"""

from collections import defaultdict

from aws_cdk import Duration
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secrets
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

from sds_data_manager.lambda_code.SDSCode.utils.science_monitoring_config import (
    MONITORED_SCIENCE_PRODUCTS,
    Alarm,
)


class ScienceMonitoringConstruct(Construct):
    """Construct for Science data freshness monitoring."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        code: lambda_.Code,
        db_secret_name: str,
        vpc: ec2.Vpc,
        rds_security_group: ec2.SecurityGroup,
        layers: list,
        alarm_email: str,
        **kwargs,
    ) -> None:
        """Create science data monitoring Lambda and CloudWatch alarms.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        code : lambda_.Code
            Lambda code bundle.
        db_secret_name : str
            Name of the secret containing database credentials.
        vpc : ec2.Vpc
            VPC for Lambda function.
        rds_security_group : ec2.SecurityGroup
            Security group for RDS access.
        layers : list
            Lambda layers for database access.
        alarm_email : str
            Email address to receive alarm notifications.
        kwargs : dict
            Keyword arguments.

        Attributes
        ----------
        sns_topic : sns.Topic
            SNS Topic for Science monitoring notifications.

        """
        super().__init__(scope, construct_id, **kwargs)

        # Create SNS topic for science monitoring notifications
        self.sns_topic = sns.Topic(
            self,
            "ScienceMonitoringTopic",
            display_name="Science Data Monitoring Notifications",
        )

        # Add email subscription
        if alarm_email:
            self.sns_topic.add_subscription(subs.EmailSubscription(alarm_email))

        # Create the monitoring Lambda function
        self.monitoring_lambda = self._create_lambda(
            code=code,
            db_secret_name=db_secret_name,
            vpc=vpc,
            rds_security_group=rds_security_group,
            layers=layers,
        )

        # Create EventBridge rule to trigger Lambda daily
        self._create_schedule_rule()

        # Create CloudWatch alarms for each monitored science product
        self._create_alarms(MONITORED_SCIENCE_PRODUCTS)

    def _create_lambda(
        self,
        code: lambda_.Code,
        db_secret_name: str,
        vpc: ec2.Vpc,
        rds_security_group: ec2.SecurityGroup,
        layers: list,
    ) -> lambda_.Function:
        """Create the science monitoring Lambda function.

        Parameters
        ----------
        code : lambda_.Code
            Lambda code bundle.
        db_secret_name : str
            Name of the secret containing database credentials.
        vpc : ec2.Vpc
            VPC for Lambda function.
        rds_security_group : ec2.SecurityGroup
            Security group for RDS access.
        layers : list
            Lambda layers for database access.

        Returns
        -------
        lambda_.Function
            The created Lambda function.

        """
        monitoring_lambda = lambda_.Function(
            self,
            id="ScienceMonitoringLambda",
            function_name="science-data-monitoring",
            code=code,
            handler=("SDSCode.pipeline_lambdas.science_data_monitoring.lambda_handler"),
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=Duration.minutes(5),
            memory_size=512,
            allow_public_subnet=True,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[rds_security_group],
            environment={
                "SECRET_NAME": db_secret_name,
                "METRIC_NAMESPACE": "IMAP/ScienceDataFreshness",
            },
            layers=layers,
        )

        # Grant access to read the database secret
        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", db_secret_name
        )
        rds_secret.grant_read(grantee=monitoring_lambda)

        # Grant permissions to publish CloudWatch metrics
        monitoring_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={
                    "StringEquals": {
                        "cloudwatch:namespace": "IMAP/ScienceDataFreshness"
                    }
                },
            )
        )

        return monitoring_lambda

    def _create_schedule_rule(self):
        """Create EventBridge rule to run Lambda daily."""
        # Run daily at 10:00 AM UTC
        schedule_rule = events.Rule(
            self,
            "ScienceMonitoringSchedule",
            rule_name="science-data-monitoring-daily",
            description="Daily check for stale science data",
            schedule=events.Schedule.cron(
                minute="0",
                hour="10",
            ),
        )

        schedule_rule.add_target(targets.LambdaFunction(self.monitoring_lambda))

    def _create_alarms(self, alarms: list[Alarm]):
        """Create CloudWatch alarms for the monitored science products.

        Creates one child alarm per product and one composite alarm per
        instrument. Only the composite alarms send SNS notifications.

        Parameters
        ----------
        alarms : list[Alarm]
            List of alarms to create.
        """
        grouped_alarms = defaultdict(list)
        for alarm in alarms:
            # Create metric for this product
            metric = cloudwatch.Metric(
                namespace="IMAP/ScienceDataFreshness",
                metric_name="DaysSinceLastFile",
                period=Duration.days(1),
                statistic="Maximum",
                dimensions_map={"Prefix": alarm.name()},
            )

            # Create a child alarm for this product. It has no action of its own;
            # the instrument's composite alarm (below) sends the SNS email.
            child_alarm = cloudwatch.Alarm(
                self,
                f"ScienceStaleData{alarm.name()}",
                alarm_name=f"science-stale-data-{alarm.name().lower()}",
                metric=metric,
                threshold=alarm.threshold_days,
                evaluation_periods=1,
                datapoints_to_alarm=1,
                comparison_operator=(
                    cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD
                ),
                treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
                alarm_description=(alarm.alarm_description()),
            )
            grouped_alarms[alarm.instrument].append(child_alarm)

        # Add a composite alarm for each instrument
        # These parent alarms will send a sns email notification if any of the
        # child alarms are triggered
        # This is to avoid sending multiple alarm emails for the same instrument.
        for instrument, alarm_group in grouped_alarms.items():
            parent_alarm = cloudwatch.CompositeAlarm(
                self,
                f"ScienceStaleData{instrument.capitalize()}",
                composite_alarm_name=f"science-stale-data-{instrument}",
                alarm_rule=cloudwatch.AlarmRule.any_of(*alarm_group),
                alarm_description=(
                    f"One or more {instrument.upper()} science products are stale. "
                    f"Check the science-stale-data-{instrument}-* child alarms in "
                    "CloudWatch to see which product triggered this."
                ),
            )

            # Add SNS action to alarm
            parent_alarm.add_alarm_action(cloudwatch_actions.SnsAction(self.sns_topic))
