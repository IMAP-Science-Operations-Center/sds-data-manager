"""Batch Processing System construct"""
# Standard
from pathlib import Path
# Installed
from aws_cdk import (
    Environment,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_s3 as s3
)
from constructs import Construct
# Local
from sds_data_manager.constructs.batch_compute_resources import FargateBatchResources
from sds_data_manager.constructs.batch_system_lambdas import ManifestCreatorLambda


class BatchProcessingSystem(Construct):
    """A complete automatic processing system utilizing S3, Lambda, and Batch all in a Step Function.
    """

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 sds_id: str,
                 processing_step_name: str,
                 lambda_code_directory: str or Path,
                 archive_bucket: s3.Bucket,
                 manifest_creator_target: str,
                 batch_resources: FargateBatchResources = None) -> None:
        """Constructor

        Parameters
        ----------
        scope : Construct
        construct_id : str
        sds_id : str
            Name suffix for stack
        processing_step_name : str
            Name of the data product to be processed by this system. This string is used to name resources.
        lambda_code_directory : str or Path
            Location of a directory containing a Dockerfile used to build Lambda runtime container images for both
            the secrets retriever and archiver lambdas.
        archive_bucket : SdcBucket
            The Sdc bucket to archive any created data products
        manifest_creator_target : str
            Name of Dockerfile target for manifest creator handler
        batch_resources : Ec2BatchCompute or FargateBatchCompute, Optional
            Compute environment, if a single compute environment should be shared between many Batch processing systems.
        """
        super().__init__(scope, construct_id)
        self.processing_step_name = processing_step_name

        ecr_authenticators = iam.Group(self, 'EcrAuthenticators')
        # Allows members of this group to get the auth token for `docker login`
        ecr.AuthorizationToken.grant_read(ecr_authenticators)
        # Add each of the Libera SDC devs to the newly created group
        # TODO: Should we allow custom usernames to be added?
        for username in self.node.try_get_context("sdc-developer-usernames"):
            user = iam.User.from_user_name(self, username, user_name=username)
            ecr_authenticators.add_user(user)
        # Ensure the ECR in our compute environment gets the proper removal policy
        # TODO: may change
        removal_policy = RemovalPolicy.DESTROY
        batch_resources.container_registry.apply_removal_policy(removal_policy)

        # Adding permissions to the dropbox and the input buckets
        archive_bucket.grant_read_write(batch_resources.batch_job_role)

        self.manifest_creator_lambda = ManifestCreatorLambda(self, "ManifestCreatorLambda",
                                                             sds_id=sds_id,
                                                             processing_step_name=processing_step_name,
                                                             archive_bucket=archive_bucket,
                                                             code_path=str(lambda_code_directory),
                                                             lambda_target=manifest_creator_target)

