"""Module containing constructs for the Schedule Job Lambda Function."""

from aws_cdk import Duration, Environment, aws_events
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secrets
from aws_cdk.aws_events import RuleTargetInput, Schedule
from aws_cdk.aws_events_targets import LambdaFunction
from constructs import Construct

from sds_data_manager.constructs.database_construct import SdpDatabase
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import (
    scheduled_job_config_reader,
)


class ScheduledJobLambda(Construct):
    """Generic Construct with customizable runtime code."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env: Environment,
        data_bucket: s3.Bucket,
        code: lambda_.Code,
        rds_construct: SdpDatabase,
        rds_security_group: ec2.SecurityGroup,
        vpc: ec2.Vpc,
        layers: list,
        **kwargs,
    ):
        """ScheduledJobLambda Constructor.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        env : Environment
            Account and region.
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

        self.scheduled_job_lambda = lambda_.Function(
            self,
            "ScheduledJobLambda",
            function_name="ScheduledJobLambda",
            code=code,
            handler="SDSCode.pipeline_lambdas.scheduled_job.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            environment=lambda_environment,
            memory_size=512,
            timeout=Duration.minutes(1),
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
        self.scheduled_job_lambda.add_to_role_policy(lambda_policy)
        data_bucket.grant_read_write(self.scheduled_job_lambda)
        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", rds_construct.secret_name
        )
        rds_secret.grant_read(grantee=self.scheduled_job_lambda)

        self.scheduled_job_lambda.add_permission(
            "AllowEventBridgeInvokeScheduled",
            principal=iam.ServicePrincipal("events.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=f"arn:aws:events:{env.region}:{env.account}:rule/ProcessingScheduledJob*",
        )

        scheduled_jobs = scheduled_job_config_reader.read_scheduled_job_config()

        for i, schedule in enumerate(scheduled_jobs.keys()):
            rule = aws_events.Rule(
                scope=scope,
                id=f"ProcessingScheduledJob-{i}",
                rule_name=f"ProcessingScheduledJob-{i}",
                description=f"Trigger scheduled processing job: {schedule}",
                schedule=Schedule.expression(schedule),
                enabled=True,
            )

            target = LambdaFunction(
                handler=self.scheduled_job_lambda,
                event=RuleTargetInput.from_object({"scheduled": schedule}),
            )
            rule.add_target(target)
