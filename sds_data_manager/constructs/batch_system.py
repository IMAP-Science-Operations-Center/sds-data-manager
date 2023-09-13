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

    Including:
    - References Input S3 buckets for Batch to read from. (External to this Construct)
    - Creates Dropbox S3 bucket for storage of generated data files and manifests
    - Creates Timeout Lambda function to set a timeout time for generating incomplete or failed input manifests
    - Creates Manifest Creator Lambda function to generate a manifest and create a batch command
    - Creates Batch compute resources with an associated source container registry.
    - Creates Manifest Mover Lambda function to determined failed or succeeded manifests
    - Creates Archiver Lambda function to validate and archive products written by Batch to the dropbox
    """

    # TODO: The parameter rds_security_group is unnecessarily specific. Rename it to archiver_sg. Alternatively,
    #   we could try to omit this entirely and retroactively add the archiver lambda to the same SG as the RDS. This
    #   comment also applies to the subnets kwarg. Together, these kwargs make the Construct signature confusing and
    #   cryptic.

    # TODO: In its current form, we are creating separate Lambda functions for each processing step archiver. However,
    #   all of these Lambdas are running the exact same code for validating and archiving data products. If we
    #   want to continue in this way without adding custom code for each archiver, it would be more efficient to
    #   create the archiver lambda outside of this construct. However, this would limit our ability to customize
    #   archiver behavior based on the current processing step.

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

