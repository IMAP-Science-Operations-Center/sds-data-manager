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
from sds_data_manager.constructs.sdc_step_function import SdcStepFunction
from sds_data_manager.constructs.event_bridge import S3EventbridgeStepFunctionsProps, S3EventbridgeStepFunctions
from sds_data_manager.constructs.batch_system_lambdas import ManifestCreatorLambda


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
                 manifest_creator_target: str,
                 batch_security_group: classmethod = None,
                 **kwargs) -> None:
        """Constructor

        Parameters
        ----------
        scope : Construct
        construct_id : str
        env : Environment
        sds_id : str
            Name suffix for stack
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        processing_step_name : str
            Name of the data product to be processed by this system. This string is used to name resources.
        lambda_code_directory : str or Path
            Location of a directory containing a Dockerfile used to build Lambda runtime container images for both
            the secrets retriever and archiver lambdas.
        archive_bucket : s3.Bucket
            The s3 bucket to archive any created data products
        manifest_creator_target : str
            Name of Dockerfile target for manifest creator handler. If not provided, no manifest file creator
            lambda is synthesized.
        batch_security_group : ec2.SecurityGroup, Optional
            Batch processor security group. Must have an ingress rule into the RDS security group
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        self.batch_resources = FargateBatchResources(self,
                                                     f"FargateBatchEnvironment-{sds_id}",
                                                     sds_id,
                                                     vpc=vpc,
                                                     security_group=batch_security_group,
                                                     processing_step_name=processing_step_name,
                                                     archive_bucket=archive_bucket,)

        self.manifest_creator_lambda = ManifestCreatorLambda(self, f"ManifestCreatorLambda-{sds_id}",
                                                             sds_id=sds_id,
                                                             processing_step_name=processing_step_name,
                                                             archive_bucket=archive_bucket,
                                                             code_path=str(lambda_code_directory),
                                                             lambda_target=manifest_creator_target)

        self.step_function = SdcStepFunction(self,
                                             f"SdcStepFunction-{sds_id}",
                                             sds_id,
                                             processing_step_name=processing_step_name,
                                             processing_system=self.manifest_creator_lambda,
                                             batch_resources=self.batch_resources)

        # Enable S3 Event Notifications to send to EventBridge
        props = S3EventbridgeStepFunctionsProps(
            state_machine=self.step_function.state_machine,
            state_machine_input={},
            source_bucket=archive_bucket
        )

        # Instantiate the construct
        S3EventbridgeStepFunctions(self, "S3EventToSF", props)

