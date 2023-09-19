"""Data Storage Stack
This is the module containing the general stack to be built for computation of different algorithms

"""
from pathlib import Path
from constructs import Construct
from aws_cdk import (
    Stack,
    Environment,
    aws_ec2 as ec2,
    aws_s3 as s3,
    aws_events as events,
    aws_events_targets as event_targets,
)

from sds_data_manager.constructs.batch_compute_resources import FargateBatchResources
from sds_data_manager.constructs.sdc_step_function import SdcStepFunction
from sds_data_manager.constructs.instrument_lambdas import InstrumentLambda


class ProcessingStep(Stack):
    """A complete automatic processing system utilizing S3, Lambda, and Batch.
    """

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 sds_id: str,
                 env: Environment,
                 vpc: ec2.Vpc,
                 processing_step_name: str,
                 lambda_code_directory: str or Path,
                 archive_bucket: s3.Bucket,
                 instrument_target: str,
                 instrument_sources,
                 batch_security_group: classmethod = None,
                 **kwargs) -> None:
        """Constructor

        Parameters
        ----------
        scope : Construct
        construct_id : str
        sds_id : str
            Name suffix for stack
        env : Environment
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking
        processing_step_name : str
            Name of the data product to be processed by this system
        lambda_code_directory : str or Path
            Lambda directory
        archive_bucket : s3.Bucket
            S3 bucket
        instrument_target : str
            Target data product
        instrument_sources : str
            Data product sources
        batch_security_group : ec2.SecurityGroup
            Batch processor security group
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        self.batch_resources = FargateBatchResources(self,
                                                     f"FargateBatchEnvironment-{sds_id}",
                                                     sds_id,
                                                     vpc=vpc,
                                                     security_group=batch_security_group,
                                                     processing_step_name=processing_step_name,
                                                     archive_bucket=archive_bucket,)

        self.instrument_lambda = InstrumentLambda(self, f"InstrumentLambda-{sds_id}",
                                                  sds_id=sds_id,
                                                  processing_step_name=processing_step_name,
                                                  archive_bucket=archive_bucket,
                                                  code_path=str(lambda_code_directory),
                                                  instrument_target=instrument_target,
                                                  instrument_sources=instrument_sources)

        self.step_function = SdcStepFunction(self,
                                             f"SdcStepFunction-{processing_step_name}",
                                             processing_step_name=processing_step_name,
                                             processing_system=self.instrument_lambda,
                                             batch_resources=self.batch_resources,
                                             instrument_target=instrument_target,
                                             archive_bucket=archive_bucket)

        # TODO: This will be a construct and also we will add to its capabilities.
        rule = events.Rule(self, "rule",
                           event_pattern=events.EventPattern(
                               source=["aws.s3"],
                               detail_type=["Object Created"],
                               detail={
                                   "bucket": {
                                       "name": [archive_bucket.bucket_name]
                                   },
                                   "object": {
                                       "key": [{
                                           "prefix": f"{instrument_sources}"
                                       }]}
                               }
                           ))

        rule.add_target(event_targets.SfnStateMachine(self.step_function.state_machine))
