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
)
from constructs import Construct
# Local
from sds_data_manager.constructs.batch_compute_resources import FargateBatchResources
from sds_data_manager.constructs.batch_system_lambdas import ArchiverLambda

from sds_data_manager.constructs.sdc_bucket import SdcBucket


class BatchProcessingSystem(Construct):
    """A complete automatic processing system utilizing S3, Lambda, and Batch all in a Step Function.
    """

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 sds_id: str,
                 env: Environment,
                 vpc: ec2.Vpc,
                 processing_step_name: str,
                 lambda_code_directory: str or Path,
                 subnets: ec2.SubnetSelection,
                 archive_bucket: SdcBucket,
                 batch_security_group: ec2.SecurityGroup = None,
                 input_buckets: list = None,
                 batch_resources: FargateBatchResources = None) -> None:
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
        db_secret_name :str
            RDS secret name for secret manager access
        subnets : ec2.SubnetSelection
            RDS subnet selection
        name_suffix : str
            If provided, this is appended to any resources requiring unique names. Default is None (no suffix).
        archive_bucket : SdcBucket
            The Sdc bucket to archive any created data products
        manifest_creator_target : str
            Name of Dockerfile target for manifest creator handler
        file_index_tables : list, Optional
            Passed on to the internal ManifestCreator lambda where it is used to query for newly arrived input data.
        batch_security_group : ec2.SecurityGroup, Optional
            Batch processing security group to allow processing to access the RDS. Must have an ingress rule on the
            RDS security group for this security group.
        input_buckets : list, Optional
            List of S3 bucket objects that contain input data to the Batch processing job. This is used to
            ensure read permissions are set properly for all buckets that the Batch processing may need to access.
        batch_has_db_access: bool, Optional
            Is true if the batch job will need to access the RDS database. Is False by default.
        batch_resources : Ec2BatchCompute or FargateBatchCompute, Optional
            Compute environment, if a single compute environment should be shared between many Batch processing systems.
        timeout_hour_offset : int, Optional
            The number of hours to offset time out time from the time of starting the step function.
            Default is 24 hours
        """
        super().__init__(scope, construct_id)

        self.processing_step_name = processing_step_name

        # Fargate is the default set of Batch Resources unless the user specifies an EC2 environment
        if batch_resources is None:
            self.batch_resources = FargateBatchResources(self, 'FargateBatchResources',
                                                         vpc=vpc,
                                                         security_group=batch_security_group,
                                                         processing_step_name=processing_step_name)
        else:
            self.batch_resources = batch_resources
        # TODO: Add DB access to the batch job if needed

        # Grant access to libera developers to push ECR Images to be used by the batch job
        dev_account = self.node.try_get_context('dev')['account']
        if env.account == dev_account:
            ecr_authenticators = iam.Group(self, 'EcrAuthenticators')
            # Allows members of this group to get the auth token for `docker login`
            ecr.AuthorizationToken.grant_read(ecr_authenticators)
            # Add each of the Libera SDC devs to the newly created group
            for username in self.node.try_get_context("sdc-developer-usernames"):
                user = iam.User.from_user_name(self, username, user_name=username)
                ecr_authenticators.add_user(user)
        # Ensure the ECR in our compute environment gets the proper removal policy (may change)
        removal_policy = RemovalPolicy.DESTROY
        self.batch_resources.container_registry.apply_removal_policy(removal_policy)

        # Dropbox bucket used for manifests (input and output) and output data products
        self.dropbox = SdcBucket(self, "DropboxBucket",
                                 env=env,
                                 bucket_name=f"{processing_step_name}-dropbox")

        # Adding permissions to the dropbox and the input buckets
        self.dropbox.bucket.grant_read_write(batch_resources.batch_job_role)

        if input_buckets is not None:
            for bucket in input_buckets:
                bucket.grant_read(batch_resources.batch_job_role)

        self.finished_trigger_function = ArchiverLambda(self, "ArchiverLambda-{sds_id}",
                                                        sds_id,
                                                        vpc=vpc,
                                                        subnets=subnets,
                                                        processing_step_name=processing_step_name,
                                                        dropbox=self.dropbox,
                                                        archive_bucket=archive_bucket,
                                                        code_path=str(lambda_code_directory),
                                                        lambda_target=f"data-archiver-{sds_id}")
