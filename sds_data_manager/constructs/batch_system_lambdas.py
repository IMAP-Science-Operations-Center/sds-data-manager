"""Module containing Constructs for Lambda functions that run in response to manifest files appearing in an S3 bucket"""
# Standard
from pathlib import Path
# Installed
from constructs import Construct
from aws_cdk import (
    aws_ec2 as ec2,
    aws_lambda as _lambda,
    aws_s3 as s3,
    aws_ecr_assets as ecr_assets,
    Duration
)


class ArchiverLambda(Construct):
    """Lambda that archives data once processing completes.

    This construct consists of a Lambda that moves data products from a dropbox s3 bucket to an archive s3 bucket
    in response to an output manifest landing in the dropbox.
    """

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 sds_id: str,
                 vpc: ec2.Vpc,
                 subnets: ec2.SubnetSelection,
                 processing_step_name: str,
                 archive_bucket: s3.Bucket,
                 code_path: str or Path,
                 lambda_target: str):
        """Constructor

        Parameters
        ----------
        scope : Construct
        construct_id : str
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        rds_security_group : ec2.SecurityGroup
            RDS security group
        subnets : ec2.SubnetSelection
            RDS subnets selection
        db_secret_name : str
            RDS secret name for secret manager access
        processing_step_name : str
            String representation of the data product being generated. e.g. program-l2a
        dropbox : SdcBucket
            s3 bucket where output manifest files will be read from
        archive_bucket : SdcBucket
            Archive bucket
        code_path : str
            Path to the Lambda code directory. This directory is assumed to contain the resources required to build
            a Docker image containing the desired lambda code. At minimum, usually a requirements file, a Dockerfile,
            and a script that serves as the entrypoint to the Docker image. The Docker image is built and distributed
            as part of the CDK deployment process according to the Dockerfile you provide. Note: you may provide a
            single Dockerfile with multiple targets for different Lambdas, allowing you to put all lambda code into a
            single directory.
        lambda_target : str, Optional
            Name of the Docker target, if multiple are defined in the Dockerfile.
        """
        super().__init__(scope, construct_id)

        # Define Dockerized lambda function (so we can skip uploading layers of zip files)
        docker_image_code = _lambda.DockerImageCode.from_image_asset(str(code_path), target=lambda_target,
                                                                     platform=ecr_assets.Platform.LINUX_AMD64)

        # Create Environment Variables
        lambda_environment = {
            "ARCHIVE_PATH": f"s3://{archive_bucket.bucket_name}",
        }

        self.lambda_function = _lambda.DockerImageFunction(self, f'Archiver-{sds_id}',
                                                           function_name=f"{processing_step_name}-data-archiver-{sds_id}",
                                                           code=docker_image_code,
                                                           environment=lambda_environment,
                                                           retry_attempts=0,
                                                           timeout=Duration.minutes(10),
                                                           memory_size=1024,
                                                           vpc=vpc,
                                                           vpc_subnets=subnets,
                                                           allow_public_subnet=True)

        archive_bucket.grant_read_write(self.lambda_function)
