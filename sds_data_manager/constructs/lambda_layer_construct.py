"""CDK construct to create a Lambda Layer."""

import aws_cdk as cdk
from aws_cdk import aws_lambda as lambda_


class IMAPLambdaLayer(lambda_.LayerVersion):
    """Lambda Layer."""

    def __init__(
        self,
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
            id=f"{id}-Layer", code=code_bundle, compatible_runtimes=[runtime], **kwargs
        )
