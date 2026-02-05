"""Configure SPICE data freshness monitoring.

This construct creates a Lambda function that runs daily to check for
stale SPICE data in specific S3 prefixes, publishes CloudWatch metrics,
and creates alarms that trigger SNS notifications when data is missing.
"""

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


class SpiceMonitoringConstruct(Construct):
    """Construct for SPICE data freshness monitoring."""

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
        ck_threshold_days: int = 4,
        spin_threshold_days: int = 4,
        sclk_threshold_days: int = 4,
        repoint_threshold_days: int = 4,
        predicted_ephemeris_threshold_days: int = 4,
        **kwargs,
    ) -> None:
        """Create SPICE data monitoring Lambda and CloudWatch alarms.

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
        ck_threshold_days : int, optional
            Number of days before CK kernel data is considered stale.
            Default is 4.
        spin_threshold_days : int, optional
            Number of days before spin file data is considered stale.
            Default is 4.
        sclk_threshold_days : int, optional
            Number of days before SCLK kernel data is considered stale.
            Default is 4.
        repoint_threshold_days : int, optional
            Number of days before repoint file data is considered stale.
            Default is 4.
        predicted_ephemeris_threshold_days : int, optional
            Number of days before predicted ephemeris data is considered
            stale. Default is 4.
        kwargs : dict
            Keyword arguments.

        Attributes
        ----------
        sns_topic : sns.Topic
            SNS Topic for SPICE monitoring notifications.

        """
        super().__init__(scope, construct_id, **kwargs)

        # Create SNS topic for SPICE monitoring notifications
        self.sns_topic = sns.Topic(
            self,
            "SpiceMonitoringTopic",
            display_name="SPICE Data Monitoring Notifications",
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
            ck_threshold_days=ck_threshold_days,
            spin_threshold_days=spin_threshold_days,
            sclk_threshold_days=sclk_threshold_days,
            repoint_threshold_days=repoint_threshold_days,
            predicted_ephemeris_threshold_days=predicted_ephemeris_threshold_days,
        )

        # Create EventBridge rule to trigger Lambda daily
        self._create_schedule_rule()

        # Create CloudWatch alarms for each monitored prefix
        self._create_alarms(
            ck_threshold_days=ck_threshold_days,
            spin_threshold_days=spin_threshold_days,
            sclk_threshold_days=sclk_threshold_days,
            repoint_threshold_days=repoint_threshold_days,
            predicted_ephemeris_threshold_days=predicted_ephemeris_threshold_days,
        )

    def _create_lambda(
        self,
        code: lambda_.Code,
        db_secret_name: str,
        vpc: ec2.Vpc,
        rds_security_group: ec2.SecurityGroup,
        layers: list,
        ck_threshold_days: int,
        spin_threshold_days: int,
        sclk_threshold_days: int,
        repoint_threshold_days: int,
        predicted_ephemeris_threshold_days: int,
    ) -> lambda_.Function:
        """Create the SPICE monitoring Lambda function.

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
        ck_threshold_days : int
            Threshold for CK kernels.
        spin_threshold_days : int
            Threshold for spin files.
        sclk_threshold_days : int
            Threshold for SCLK kernels.
        repoint_threshold_days : int
            Threshold for repoint files.
        predicted_ephemeris_threshold_days : int
            Threshold for predicted ephemeris files.

        Returns
        -------
        lambda_.Function
            The created Lambda function.

        """
        monitoring_lambda = lambda_.Function(
            self,
            id="SpiceMonitoringLambda",
            function_name="spice-data-monitoring",
            code=code,
            handler=("SDSCode.pipeline_lambdas.spice_data_monitoring.lambda_handler"),
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
                "METRIC_NAMESPACE": "IMAP/SpiceDataFreshness",
                "CK_THRESHOLD_DAYS": str(ck_threshold_days),
                "SPIN_THRESHOLD_DAYS": str(spin_threshold_days),
                "SCLK_THRESHOLD_DAYS": str(sclk_threshold_days),
                "REPOINT_THRESHOLD_DAYS": str(repoint_threshold_days),
                "PREDICTED_EPHEMERIS_THRESHOLD_DAYS": str(
                    predicted_ephemeris_threshold_days
                ),
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
                    "StringEquals": {"cloudwatch:namespace": "IMAP/SpiceDataFreshness"}
                },
            )
        )

        return monitoring_lambda

    def _create_schedule_rule(self):
        """Create EventBridge rule to run Lambda daily."""
        # Run daily at 10:00 AM UTC
        schedule_rule = events.Rule(
            self,
            "SpiceMonitoringSchedule",
            rule_name="spice-data-monitoring-daily",
            description="Daily check for stale SPICE data",
            schedule=events.Schedule.cron(
                minute="0",
                hour="10",
            ),
        )

        schedule_rule.add_target(targets.LambdaFunction(self.monitoring_lambda))

    def _create_alarms(
        self,
        ck_threshold_days: int,
        spin_threshold_days: int,
        sclk_threshold_days: int,
        repoint_threshold_days: int,
        predicted_ephemeris_threshold_days: int,
    ):
        """Create CloudWatch alarms for each monitored prefix.

        Parameters
        ----------
        ck_threshold_days : int
            Threshold for CK kernels.
        spin_threshold_days : int
            Threshold for spin files.
        sclk_threshold_days : int
            Threshold for SCLK kernels.
        repoint_threshold_days : int
            Threshold for repoint files.
        predicted_ephemeris_threshold_days : int
            Threshold for predicted ephemeris files.

        """
        # Configuration for each alarm
        alarm_configs = [
            {
                "name": "CK_Kernels",
                "description": ("Attitude history and pointing attitude kernels"),
                "threshold": ck_threshold_days,
            },
            {
                "name": "Spin_Files",
                "description": "Spacecraft spin files",
                "threshold": spin_threshold_days,
            },
            {
                "name": "SCLK_Kernels",
                "description": "Spacecraft clock kernels",
                "threshold": sclk_threshold_days,
            },
            {
                "name": "Repoint_Files",
                "description": "Spacecraft repoint files",
                "threshold": repoint_threshold_days,
            },
            {
                "name": "Predicted_Ephemeris",
                "description": "Predicted ephemeris kernels",
                "threshold": predicted_ephemeris_threshold_days,
            },
        ]

        for config in alarm_configs:
            prefix_name = config["name"]
            description = config["description"]
            threshold = config["threshold"]

            # Create metric for this prefix
            metric = cloudwatch.Metric(
                namespace="IMAP/SpiceDataFreshness",
                metric_name="DaysSinceLastFile",
                period=Duration.days(1),
                statistic="Maximum",
                dimensions_map={"Prefix": prefix_name},
            )

            # Create alarm
            alarm = cloudwatch.Alarm(
                self,
                f"SpiceStaleData{prefix_name}",
                alarm_name=f"spice-stale-data-{prefix_name.lower()}",
                metric=metric,
                threshold=threshold,
                evaluation_periods=1,
                datapoints_to_alarm=1,
                comparison_operator=(
                    cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD
                ),
                treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
                alarm_description=(
                    f"Alarm when {description} have not been "
                    f"updated in {threshold} days"
                ),
            )

            # Add SNS action to alarm
            alarm.add_alarm_action(cloudwatch_actions.SnsAction(self.sns_topic))
