"""CDK construct to create a Lambda Layer."""

import aws_cdk as cdk
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class IMAPLambdaLayer(lambda_.LayerVersion):
    """Lambda Layer."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        layer_dependencies_dir: str,
        runtime=lambda_.Runtime.PYTHON_3_12,
        **kwargs,
    ) -> None:
        """Create layer.

        In layer code directory, there should exist a requirements.txt file
        which is used to install the dependencies for the lambda layer.


        Parameters
        ----------
        scope : Construct
            The App object in which to create this Construct
        id : str
            A unique string identifier for this construct
        layer_dependencies_dir : str
            Directory containing the lambda layer requirements.txt file
        runtime : lambda_.Runtime, optional
            Lambda runtime, by default lambda_.Runtime.PYTHON_3_12
        kwargs : dict
            Keyword arguments
        """
        code_bundle = lambda_.Code.from_asset(
            layer_dependencies_dir,
            bundling=cdk.BundlingOptions(
                image=runtime.bundling_image,
                # NOTE: We need to use the x86_64 architecture for the lambda layer
                #      otherwise people with mac's will produce different shared object
                #      files from those produced on our CI runners.
                #      To debug you can look at the psycopg2 linked binaries in the
                #      assets by adding the following to the commands below:
                #      ls -al /asset-output/python/psycopg2
                platform="linux/amd64",
                environment={"DOCKER_DEFAULT_PLATFORM": "linux/amd64"},
                command=[
                    "bash",
                    "-c",
                    (
                        "pip install -r requirements.txt -t /asset-output/python && "
                        "cp -au . /asset-output/python"
                    ),
                ],
            ),
        )

        super().__init__(
            scope,
            id=f"{id}-Layer",
            code=code_bundle,
            compatible_runtimes=[runtime],
            compatible_architectures=[lambda_.Architecture.X86_64],
            **kwargs,
        )
