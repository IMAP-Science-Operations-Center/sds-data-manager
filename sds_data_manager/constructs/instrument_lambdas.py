"""Module containing constructs for instrumenting Lambda functions."""

from pathlib import Path

from aws_cdk import Duration, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_python_alpha as lambda_alpha
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secrets
from constructs import Construct

from sds_data_manager.constructs.batch_compute_resources import FargateBatchResources
from sds_data_manager.stacks.database_stack import SdpDatabase


class InstrumentLambda(Construct):
    """Generic Construct with customizable runtime code"""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        data_bucket: s3.Bucket,
        code_path: str or Path,
        instrument: str,
        instrument_downstream: dict,
        batch_resources: FargateBatchResources,
        rds_stack: SdpDatabase,
        rds_security_group: ec2.SecurityGroup,
        subnets: ec2.SubnetSelection,
        vpc: ec2.Vpc,
    ):
        """
        InstrumentLambda Constructor.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        data_bucket: s3.Bucket
            S3 bucket
        code_path : str or Path
            Path to the Lambda code directory
        instrument : str
            Instrument
        instrument_downstream : dict
            Instrument downstream dependents of given instruments
        batch_resources: FargateBatchResources
            Fargate compute environment
        rds_stack: SdpDatabase
            Database stack
        rds_security_group : ec2.SecurityGroup
            RDS security group
        subnets : ec2.SubnetSelection
            RDS subnet selection.
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        """

        super().__init__(scope, construct_id)

        # Batch Job Inputs
        stack = Stack.of(self)
        job_definition_arn = (
            f"arn:aws:batch:{stack.region}:{stack.account}:job-definition/"
            f"{batch_resources.job_definition_name}"
        )
        job_queue_arn = (
            f"arn:aws:batch:{stack.region}:{stack.account}:job-queue/"
            f"{batch_resources.job_queue_name}"
        )

        # Define Lambda Environment Variables
        # TODO: if we need more variables change so we can pass as input
        lambda_environment = {
            "INSTRUMENT": instrument,
            "INSTRUMENT_DOWNSTREAM": f"{instrument_downstream}",
            "BATCH_JOB_DEFINITION": job_definition_arn,
            "BATCH_JOB_QUEUE": job_queue_arn,
            "SECRET_ARN": rds_stack.rds_creds.secret_arn,
        }

        self.instrument_lambda = lambda_alpha.PythonFunction(
            self,
            "InstrumentLambda",
            function_name="InstrumentLambda",
            entry=str(code_path),
            index="batch_starter.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            environment=lambda_environment,
            retry_attempts=0,
            memory_size=512,
            timeout=Duration.minutes(1),
            vpc=vpc,
            vpc_subnets=subnets,
            security_groups=[rds_security_group],
            allow_public_subnet=True,
        )

        data_bucket.grant_read_write(self.instrument_lambda)

        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", rds_stack.secret_name
        )
        rds_secret.grant_read(grantee=self.instrument_lambda)
