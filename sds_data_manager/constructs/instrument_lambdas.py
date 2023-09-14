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


class InstrumentLambda(Construct):
    """Generic Construct with customizable runtime code
    """

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 sds_id:str,
                 processing_step_name: str,
                 archive_bucket: s3.Bucket,
                 code_path: str or Path,
                 instrument_target: str,
                 instrument_sources):
        """InstrumentLambda Constructor

        Parameters
        ----------
        scope : Construct
        construct_id : str
        sds_id : str
            Name suffix for stack
        processing_step_name : str
            Processing step name. e.g. Data product level name (gets prepended to resource names).
        archive_bucket: TODO add
        code_path : str or Path
            Path to the Lambda code directory. This directory is assumed to contain the resources required to build
            a Docker image containing the desired lambda code. At minimum, usually a requirements file, a Dockerfile,
            and a script that serves as the entrypoint to the Docker image. The Docker image is built and distributed
            as part of the CDK deployment process according to the Dockerfile you provide. Note: you may provide a
            single Dockerfile with multiple targets for different Lambdas, allowing you to put all lambda code into a
            single directory.
        instrument_creator_target : str
            Name of Dockerfile target for Lambda function handler.
        """
        super().__init__(scope, construct_id)


        # Create Environment Variables
        lambda_environment = {
            "PROCESSING_PATH": f"archive-{sds_id}",
            "INSTRUMENT_SOURCES": instrument_sources,
            "PROCESSING_NAME": processing_step_name,
            "INSTRUMENT_TARGET": instrument_target
        }

        self.instrument_lambda = lambda_alpha_.PythonFunction(
            self,
            id=f"InstrumentLambda-{processing_step_name}",
            function_name=f"{processing_step_name}",
            entry=str(code_path),
            index=f"instruments/{instrument_target}.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=Duration.minutes(10),
            memory_size=1000,
            environment=lambda_environment
        )

        archive_bucket.grant_read_write(self.instrument_lambda)
