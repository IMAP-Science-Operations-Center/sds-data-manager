from typing import Optional

from aws_cdk import (
    Duration,
    Stack,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from constructs import Construct


class LambdaWithDockerImageStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        lambda_name: str,
        managed_policy_names: dict,
        lambda_code_folder: str,
        timeout: int = 60,
        lambda_environment_vars: Optional[dict] = None,
        **kwargs,
    ):
        """This create role for lambda with given policies
        and creates a lambda image from the given folder.
        It also sets the timeout and environment variables.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        lambda_name : str
            The name of the lambda.
        managed_policy_names : dict
            AWS managed policies to be attached to the role.
        lambda_code_folder : str
            The folder where the lambda code is located. In that folder,
            a Dockerfile is required.
        timeout : int, optional
            The timeout of the lambda. The default is 60 seconds.
        lambda_environment_vars : dict, optional
            The environment variables of the lambda. The default is None.
        """
        super().__init__(scope, construct_id, **kwargs)
        if lambda_environment_vars is None:
            lambda_environment_vars = {}

        # create role, add permissions
        iam_role_name = f"{lambda_name}-role"
        self.execution_role = iam.Role(
            self,
            iam_role_name,
            role_name=iam_role_name,
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )

        for policy in managed_policy_names:
            self.execution_role.add_managed_policy(
                iam.ManagedPolicy.from_aws_managed_policy_name(policy)
            )

        # create lambda image
        lambda_image = lambda_.DockerImageCode.from_image_asset(
            directory=lambda_code_folder,
            build_args={"--platform": "linux/amd64"},
        )

        # create lambda
        self.fn = lambda_.DockerImageFunction(
            self,
            lambda_name,
            function_name=lambda_name,
            code=lambda_image,
            role=self.execution_role,
            timeout=Duration.seconds(timeout),
            environment=lambda_environment_vars,
            architecture=lambda_.Architecture.ARM_64,
        )
