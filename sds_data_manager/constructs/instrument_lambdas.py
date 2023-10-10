"""Module containing constructs for instrumenting Lambda functions."""

from pathlib import Path

from aws_cdk import Duration
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_python_alpha as lambda_alpha_
from aws_cdk import aws_s3 as s3
from constructs import Construct


class InstrumentLambda(Construct):
    """Generic Construct with customizable runtime code"""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        processing_step_name: str,
        data_bucket: s3.Bucket,
        code_path: str or Path,
        instrument_target: str,
        instrument_sources: str,
    ):
        """
        InstrumentLambda Constructor.

        Parameters
        ----------
        scope : Construct
        construct_id : str
        processing_step_name : str
            Processing step name
        data_bucket: s3.Bucket
            S3 bucket
        code_path : str or Path
            Path to the Lambda code directory
        instrument_target : str
            Target data product (i.e. expected product)
        instrument_sources : str
            Data product sources (i.e. dependencies)
        """

        super().__init__(scope, construct_id)

        # Define Lambda Environment Variables
        # TODO: if we need more variables change so we can pass as input
        lambda_environment = {
            "S3_BUCKET": f"{data_bucket.bucket_name}",
            "S3_KEY_PATH": instrument_sources,
            "INSTRUMENT_TARGET": instrument_target,
            "PROCESSING_NAME": processing_step_name,
            "OUTPUT_PATH": f"s3://{data_bucket.bucket_name}/{instrument_target}",
        }

        # TODO: Add Lambda layers for more libraries (or Dockerize)
        self.instrument_lambda = lambda_alpha_.PythonFunction(
            self,
            id=f"InstrumentLambda-{processing_step_name}",
            function_name=f"{processing_step_name}",
            entry=str(code_path),
            index=f"instruments/{instrument_target.lower()}.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            timeout=Duration.seconds(10),
            memory_size=512,
            environment=lambda_environment,
        )

        data_bucket.grant_read_write(self.instrument_lambda)
