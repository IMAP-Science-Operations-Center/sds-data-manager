"""Processing Stack
This is the module containing the general stack to be built for
computation of different algorithms
"""
from pathlib import Path

from aws_cdk import Environment, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as event_targets
from aws_cdk import aws_s3 as s3
from constructs import Construct

from sds_data_manager.constructs.batch_compute_resources import FargateBatchResources
from sds_data_manager.constructs.instrument_lambdas import InstrumentLambda
from sds_data_manager.constructs.sdc_step_function import SdcStepFunction


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
                 data_bucket: s3.Bucket,
                 instrument_target: str,
                 instrument_sources: str,
                 repo: ecr.Repository,
                 **kwargs) -> None:
        """Constructor

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct
        sds_id : str
            Name suffix for stack
        env : Environment
            The AWS environment (account/region) where the stack will be deployed
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking
        processing_step_name : str
            Name of the data product to be processed by this system
        lambda_code_directory : str or Path
            Lambda directory
        data_bucket : s3.Bucket
            S3 bucket
        instrument_target : str
            Target data product
        instrument_sources : str
            Data product sources
        repo : ecr.Repository
            Container repo
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        self.batch_resources = FargateBatchResources(self,
                                                     f"FargateBatchEnvironment-{sds_id}",
                                                     sds_id,
                                                     vpc=vpc,
                                                     processing_step_name=processing_step_name,
                                                     data_bucket=data_bucket,
                                                     repo=repo)

        self.instrument_lambda = InstrumentLambda(self, f"InstrumentLambda-{sds_id}",
                                                  processing_step_name=processing_step_name,
                                                  data_bucket=data_bucket,
                                                  code_path=str(lambda_code_directory),
                                                  instrument_target=instrument_target,
                                                  instrument_sources=instrument_sources)

        self.step_function = SdcStepFunction(self,
                                             f"SdcStepFunction-{processing_step_name}",
                                             processing_step_name=processing_step_name,
                                             processing_system=self.instrument_lambda,
                                             batch_resources=self.batch_resources,
                                             instrument_target=instrument_target,
                                             data_bucket=data_bucket)

        # TODO: This will be a construct and also we will add to its capabilities.
        rule = events.Rule(self, "rule",
                           event_pattern=events.EventPattern(
                               source=["aws.s3"],
                               detail_type=["Object Created"],
                               detail={
                                   "bucket": {
                                       "name": [data_bucket.bucket_name]
                                   },
                                   "object": {
                                       "key": [{
                                           "prefix": f"{instrument_sources}"
                                       }]}
                               }
                           ))

        rule.add_target(event_targets.SfnStateMachine(self.step_function.state_machine))
