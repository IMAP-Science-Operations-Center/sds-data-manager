"""Module containing constructs for instrumenting Lambda functions."""

from aws_cdk import Duration, Environment
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secrets
from aws_cdk import aws_sqs as sqs
from aws_cdk.aws_lambda_event_sources import SqsEventSource
from constructs import Construct

from sds_data_manager.constructs.database_construct import SdpDatabase


class BatchStarterLambda(Construct):
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
        subnets: ec2.SubnetSelection,
        vpc: ec2.Vpc,
        sqs_queue: sqs.Queue,
        layers: list,
        **kwargs,
    ):
        """BatchStarterLambda Constructor.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        env : Environment
            Account and region
        data_bucket: s3.Bucket
            S3 bucket
        code : lambda_.Code
            Lambda code bundle
        rds_construct: SdpDatabase
            Database stack
        rds_security_group : ec2.SecurityGroup
            RDS security group
        subnets : ec2.SubnetSelection
            RDS subnet selection.
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        sqs_queue: sqs.Queue
            A FIFO queue to trigger the lambda with.
        layers : list
            List of Lambda layers cdk.cdfnOutput names
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        # Define Lambda Environment Variables
        # TODO: if we need more variables change so we can pass as input
        lambda_environment = {
            "S3_BUCKET": f"{data_bucket.bucket_name}",
            "SECRET_NAME": rds_construct.rds_creds.secret_name,
            "ACCOUNT": f"{env.account}",
            "REGION": f"{env.region}",
        }

        self.instrument_lambda = lambda_.Function(
            self,
            "BatchStarterLambda",
            function_name="BatchStarterLambda",
            code=code,
            handler="SDSCode.batch_starter.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            environment=lambda_environment,
            memory_size=512,
            timeout=Duration.minutes(1),
            vpc=vpc,
            vpc_subnets=subnets,
            security_groups=[rds_security_group],
            allow_public_subnet=True,
            layers=layers,
            architecture=lambda_.Architecture.ARM_64,
        )

        # Permissions to send event to EventBridge
        # and submit batch job
        lambda_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["events:PutEvents", "batch:SubmitJob"],
            resources=[
                "*",
            ],
        )
        self.instrument_lambda.add_to_role_policy(lambda_policy)

        data_bucket.grant_read_write(self.instrument_lambda)

        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", rds_construct.secret_name
        )
        rds_secret.grant_read(grantee=self.instrument_lambda)

        # This sets up the lambda to be triggered by the SQS queue. Since this is a FIFO
        # queue, each instrument will have messages processed in order. However,
        # different instruments will be processed in parallel, with multiple instances
        # of the batch_starter lambda.
        self.instrument_lambda.add_event_source(SqsEventSource(sqs_queue))
