"""Data Storage Stack
This is the module containing the general stack to be built for computation of different algorithms

"""
# Standard
from pathlib import Path
# Installed
from constructs import Construct
from aws_cdk import (
    Stack,
    Environment,
    aws_ec2 as ec2,
    aws_s3 as s3
)
# Local
from sds_data_manager.constructs.batch_compute_resources import FargateBatchResources
from sds_data_manager.constructs.batch_system import BatchProcessingSystem
from sds_data_manager.constructs.sdc_step_function import SdcStepFunction


class ProcessingStep(Stack):
    """A complete automatic processing system utilizing S3, Lambda, and Batch.

    Including:
    -Optional manifest file creator that creates input manifests to trigger Batch job. If no manifest creator target
        is provided, input manifest must be created and submitted to the dropbox by some other mechanism.
    - Input s3 buckets for Batch to read from. The batch system does not typically alter the data in the input buckets.
    - Input s3 buckets for Batch to read from.
    - Dropbox s3 bucket that triggers Lambdas to start Batch jobs and pick up generated files in response to manifests.
    - Lambda function to start Batch jobs based on input manifests appearing in dropbox (s3 triggered).
    - Batch compute environment and source container registry.
    - Lambda function to validate and archive products written by Batch to the dropbox (s3 triggered).
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
                 manifest_creator_target: str,
                 batch_security_group: classmethod = None,
                 **kwargs) -> None:
        """Constructor

        Parameters
        ----------
        scope : Construct
        construct_id : str
        env : Environment
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        processing_step_name : str
            Name of the data product to be processed by this system. This string is used to name resources.
        lambda_code_directory : str or Path
            Location of a directory containing a Dockerfile used to build Lambda runtime container images for both
            the secrets retriever and archiver lambdas.
        rds_security_group : ec2.SecurityGroup
            RDS security group
        subnets : ec2.SubnetSelection
            RDS subnet selection. e.g. ec2.SubnetSelection.PUBLIC
        db_secret_name : str
            RDS secret name for secret manager access
        archive_bucket : s3.Bucket
            The s3 bucket to archive any created data products
        manifest_creator_target : str
            Name of Dockerfile target for manifest creator handler. If not provided, no manifest file creator
            lambda is synthesized.
        file_index_tables : list, Optional
            Passed on to the internal ManifestCreator lambda where it is used to query for newly arrived input data.
        batch_security_group : ec2.SecurityGroup, Optional
            Batch processor security group. Must have an ingress rule into the RDS security group
        name_suffix : str, Optional
            If provided, this is appended to any resources requiring unique names. Default is None (no suffix).
        input_buckets : list, Optional
            List of S3 bucket objects that contain input data to the Batch processing job. This is used to
            ensure read permissions are set properly.
        upstream_sns_topics : list, Optional
            List of SNS topics that feed into the manifest file creator queue.
        timeout_hour_offset : int, Optional
            The number of hours to offset time out time from the time of starting the step function.
            Default is 24 hours
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        # The compute environment is an optional input to the BatchProcessingSystem construct.
        self.batch_resources = FargateBatchResources(self,
                                                     f"FargateBatchEnvironment-{sds_id}",
                                                     sds_id,
                                                     vpc=vpc,
                                                     security_group=batch_security_group,
                                                     processing_step_name=processing_step_name)

        # The processing system sets up a Batch job to respond to input manifests submitted to its dropbox.
        self.processing_system = BatchProcessingSystem(self,
                                                       f"BatchProcessor-{sds_id}",
                                                       sds_id,
                                                       processing_step_name=processing_step_name,
                                                       lambda_code_directory=lambda_code_directory,
                                                       archive_bucket=archive_bucket,
                                                       manifest_creator_target=manifest_creator_target,
                                                       batch_resources=self.batch_resources)

        self.step_function = SdcStepFunction(self,
                                             f"SdcStepFunction-{sds_id}",
                                             sds_id,
                                             processing_step_name=processing_step_name,
                                             processing_system=self.processing_system,
                                             batch_resources=self.batch_resources)

        # Schedule (add this to create EventBridge cron job)
        # state_machine_schedule = events.Schedule.cron(year='*', month='*', day='*', hour='12', minute='0')
        #
        # events.Rule(self, "CdkStateMachineScheduleRule",
        #             description="Run cdk state machine.",
        #             schedule=state_machine_schedule,
        #             targets=[event_targets.SfnStateMachine(self.state_machine)])
