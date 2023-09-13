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
    aws_events as events,
    aws_events_targets as targets,
    aws_ec2 as ec2,
    aws_s3 as s3,
    aws_stepfunctions as sfn
)

# Local
from sds_data_manager.constructs.batch_compute_resources import FargateBatchResources
from sds_data_manager.constructs.batch_system import BatchProcessingSystem
from sds_data_manager.constructs.sdc_step_function import SdcStepFunction
from sds_data_manager.constructs.event_bridge import S3EventToStepFunctionConstruct


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

        # Enable S3 Event Notifications to send to EventBridge
        s3_event_to_sf = S3EventToStepFunctionConstruct(self, "S3EventToSF", archive_bucket, self.step_function)

        #
        # archive_bucket.add_event_notification(s3.EventType.OBJECT_CREATED, s3.Notifications(
        #     destination=events.EventBridgeDestination(),
        #     filter=s3.NotificationKeyFilter()
        # ))
        #
        # # Create an EventBridge rule that listens for the S3 bucket event
        # event_pattern = events.EventPattern(
        #     source=["aws.s3"],
        #     detail_type=["AWS API Call via CloudTrail"],
        #     detail={
        #         "eventName": ["PutObject", "CompleteMultipartUpload"],
        #         "eventSource": ["s3.amazonaws.com"],
        #         "requestParameters": {
        #             "bucketName": [archive_bucket.bucket_name]
        #         }
        #     }
        # )
        #
        # rule = events.Rule(self, "S3ObjectCreatedRule",
        #                    event_pattern=event_pattern,
        #                    description="Listen to S3 object created events.")
        #
        # # Set the target of the rule to your Step Function
        # rule.add_target(targets.SfnStateMachine(self.step_function))

