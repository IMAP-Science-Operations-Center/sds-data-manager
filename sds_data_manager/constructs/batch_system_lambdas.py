"""Module containing Constructs for Lambda functions that run in response to manifest files appearing in an S3 bucket"""
"""Module containing Constructs for Lambda functions that run in response to manifest files appearing in an S3 bucket"""
# Standard
import json
from pathlib import Path
from typing import List
# Installed
from constructs import Construct
from aws_cdk import (
    aws_lambda_python_alpha as lambda_alpha_,
    aws_lambda as lambda_,
    aws_lambda as _lambda,
    aws_s3 as s3,
    aws_ecr_assets as ecr_assets,
    Duration
)


class ManifestCreatorLambda(Construct):
    """Generic manifest creator Construct with customizable runtime code

    Sets up a Lambda to create manifest files, providing either a file index Dynamo table or a SQS queue via
    environment variables for the Lambda to utilize at runtime.
    """

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 sds_id:str,
                 processing_step_name: str,
                 archive_bucket: s3.Bucket,
                 code_path: str or Path,
                 lambda_target: str):
        """ManifestCreatorLambda Constructor

        Parameters
        ----------
        scope : Construct
        construct_id : str
        processing_step_name : str
            Processing step name. e.g. Data product level name (gets prepended to resource names).
        code_path : str or Path
            Path to the Lambda code directory. This directory is assumed to contain the resources required to build
            a Docker image containing the desired lambda code. At minimum, usually a requirements file, a Dockerfile,
            and a script that serves as the entrypoint to the Docker image. The Docker image is built and distributed
            as part of the CDK deployment process according to the Dockerfile you provide. Note: you may provide a
            single Dockerfile with multiple targets for different Lambdas, allowing you to put all lambda code into a
            single directory.
        lambda_target : str
            Name of Dockerfile target for Lambda function handler.
        dropbox : SdcBucket
            Sdc bucket where resulting input manifest file will be written
        db_secret_name : str, Optional
            Used to provide access to the RDS DB for checking for existing data
        input_buckets : List[s3.Bucket], Optional
            List of buckets from which data might be pulled. Lambda is granted read permission on these buckets in
            order to examine the input data (e.g. checksums).
        file_index_tables : List[ddb.Table], Optional
            List of Dynamo DB tables to pass into the ManifestCreator Lambda (as a comma-separated environment variable)
            FILE_INDEX_TABLE_NAMES to allow the Lambda to query the file index.
        """
        super().__init__(scope, construct_id)


        # Create Environment Variables
        lambda_environment = {
            "PROCESSING_PATH": f"s3://{archive_bucket.bucket_name}/processing",
            "DATA_PRODUCT_NAME": processing_step_name
        }

        # self.lambda_function = lambda_alpha_.PythonFunction(
        #     self,
        #     id="ManifestCreatorLambda",
        #     function_name=f"manifest-{sds_id}",
        #     entry=str(code_path),
        #     index="SDSCode/instruments/l1a_Codice",
        #     handler="lambda_handler",
        #     runtime=lambda_.Runtime.PYTHON_3_9,
        #     timeout=Duration.minutes(10),
        #     memory_size=1000,
        #     environment=lambda_environment
        # )

        # # Define Dockerized lambda function
        # docker_image_code = _lambda.DockerImageCode.from_image_asset(str(code_path), target=lambda_target,
        #                                                              platform=ecr_assets.Platform.LINUX_AMD64)
        #
        # self.lambda_function = _lambda.DockerImageFunction(self, 'ManifestCreatorLambda',
        #                                                    function_name="l1a_Codice",
        #                                                    code=docker_image_code,
        #                                                    environment=lambda_environment,
        #                                                    retry_attempts=0,
        #                                                    memory_size=1024,
        #                                                    timeout=Duration.minutes(10))

        # Manifest Creator Lambda needs both read and write to the dropbox to list objects as well as write manifests
        archive_bucket.bucket.grant_read_write(self.lambda_function)
