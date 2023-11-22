"""Processing Stack
This is the module containing the general stack to be built for
computation of different algorithms
"""
from pathlib import Path

from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as event_targets
from aws_cdk import aws_s3 as s3
from constructs import Construct

from sds_data_manager.constructs.batch_compute_resources import FargateBatchResources
from sds_data_manager.constructs.instrument_lambdas import InstrumentLambda
from sds_data_manager.constructs.sdc_step_function import SdcStepFunction
from sds_data_manager.stacks.database_stack import SdpDatabase
from sds_data_manager.stacks.efs_stack import EFSStack


class ProcessingStep(Stack):
    """A complete automatic processing system utilizing S3, Lambda, and Batch."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        processing_step_name: str,
        lambda_code_directory: str or Path,
        data_bucket: s3.Bucket,
        instrument: str,
        instrument_downstream: dict,
        repo: ecr.Repository,
        rds_security_group: ec2.SecurityGroup,
        rds_stack: SdpDatabase,
        efs_instance: EFSStack,
        account_name: str,
        **kwargs,
    ) -> None:
        """Constructor

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking
        processing_step_name : str
            Name of the data product to be processed by this system
        lambda_code_directory : str or Path
            Lambda directory
        data_bucket : s3.Bucket
            S3 bucket
        instrument : str
            Instrument
        instrument_downstream : dict
            Downstream dependents of given instrument
            Example:
            {'l0': [{'instrument': '<instrument>', 'level': '<level>'}],
            'l1a': [{'instrument': '<instrument>', 'level': '<level>'}, {...}]}
        repo : ecr.Repository
            Container repo
        rds_security_group : ec2.SecurityGroup
            RDS security group
        rds_stack : SdpDatabase
            RDS stack
        efs_instance: efs.FileSystem
            EFS stack object
        account_name: str
            account name such as 'dev' or 'prod'
        """
        super().__init__(scope, construct_id, **kwargs)

        self.batch_resources = FargateBatchResources(
            self,
            "FargateBatchEnvironment",
            vpc=vpc,
            processing_step_name=processing_step_name,
            data_bucket=data_bucket,
            repo=repo,
            db_secret_name=rds_stack.secret_name,
            efs_instance=efs_instance,
            account_name=account_name,
        )

        self.step_function = SdcStepFunction(
            self,
            f"SdcStepFunction-{processing_step_name}",
            processing_step_name=processing_step_name,
            batch_resources=self.batch_resources,
            data_bucket=data_bucket,
            db_secret_name=rds_stack.secret_name,
        )

        self.instrument_lambda = InstrumentLambda(
            self,
            "InstrumentLambda",
            data_bucket=data_bucket,
            code_path=str(lambda_code_directory),
            instrument=instrument,
            instrument_downstream=instrument_downstream,
            step_function_stack=self.step_function,
            rds_stack=rds_stack,
            rds_security_group=rds_security_group,
            subnets=rds_stack.rds_subnet_selection,
            vpc=vpc,
        )

        # Kicks off Step Function as a result of object ingested into directories in
        # s3 bucket (instrument_sources).
        # TODO: Right now these directories are created manually in the s3 bucket.
        #  Add code so that they are not.
        rule = events.Rule(
            self,
            f"Rule-{processing_step_name}",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [data_bucket.bucket_name]},
                    "object": {
                        "key": [
                            {"prefix": f"{instrument}/{source}"}
                            for source in instrument_downstream
                        ]
                    },
                },
            ),
        )

        rule.add_target(
            event_targets.LambdaFunction(self.instrument_lambda.instrument_lambda)
        )
