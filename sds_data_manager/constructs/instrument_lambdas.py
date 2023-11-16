"""Module containing constructs for instrumenting Lambda functions."""

from pathlib import Path

from aws_cdk import Duration
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secrets
from constructs import Construct

from sds_data_manager.constructs.sdc_step_function import SdcStepFunction
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
        step_function_stack: SdcStepFunction,
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
        step_function_stack: SdcStepFunction
            Step function stack
        rds_security_group : ec2.SecurityGroup
            RDS security group
        rds_stack: SdpDatabase
            Database stack
        subnets : ec2.SubnetSelection
            RDS subnet selection.
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        """

        super().__init__(scope, construct_id)

        # Define Lambda Environment Variables
        # TODO: if we need more variables change so we can pass as input
        lambda_environment = {
            "INSTRUMENT": f"{instrument}",
            "INSTRUMENT_DOWNSTREAM": f"{instrument_downstream}",
            "STATE_MACHINE_ARN": step_function_stack.state_machine.state_machine_arn,
            "SECRET_ARN": rds_stack.rds_creds.secret_arn,
        }

        # Define Dockerized lambda function
        docker_image_code = _lambda.DockerImageCode.from_image_asset(
            directory=str(code_path), platform=ecr_assets.Platform.LINUX_AMD64
        )

        self.instrument_lambda = _lambda.DockerImageFunction(
            self,
            "InstrumentLambda",
            function_name="InstrumentLambda",
            code=docker_image_code,
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
        self.instrument_lambda.add_to_role_policy(step_function_stack.execution_policy)

        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", rds_stack.secret_name
        )
        rds_secret.grant_read(grantee=self.instrument_lambda)
