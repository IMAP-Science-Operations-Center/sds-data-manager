"""Module containing Constructs for instsrument Lambda functions"""
from pathlib import Path
from constructs import Construct
from aws_cdk import (
    aws_lambda_python_alpha as lambda_alpha_,
    aws_lambda as lambda_,
    aws_s3 as s3,
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
                 instrument_sources: str):
        """InstrumentLambda Constructor

        Parameters
        ----------
        scope : Construct
        construct_id : str
        sds_id : str
            Name suffix for stack
        processing_step_name : str
            Processing step name
        archive_bucket: s3.Bucket
            S3 bucket
        code_path : str or Path
            Path to the Lambda code directory
        instrument_target : str
            Target data product
        instrument_sources : str
            Data product sources
        """
        super().__init__(scope, construct_id)

        # Create Environment Variables
        lambda_environment = {
            "PROCESSING_PATH": f"archive-{sds_id}",
            "INSTRUMENT_SOURCES": instrument_sources,
            "INSTRUMENT_TARGET": instrument_target,
            "PROCESSING_NAME": processing_step_name,
            "OUTPUT_PATH":f"s3://{archive_bucket.bucket_name}/{instrument_target}"
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
