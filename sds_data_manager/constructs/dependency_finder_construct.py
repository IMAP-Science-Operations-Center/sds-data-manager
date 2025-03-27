"""Configure the Dependency lambda and API."""

from aws_cdk import Duration
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

from .api_gateway_construct import ApiGateway


class DependencyFinder(Construct):
    """Construct for getting upstream and downstream dependencies."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        code: lambda_.Code,
        layers: list,
        api: ApiGateway,
        **kwargs,
    ):
        """DependencyFinder Constructor.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        code : lambda_.Code
            Lambda code bundle
        layers : list
            List of Lambda layers cdk.cdfnOutput names
        api : ApiGateway
            The API Gateway construct
        kwargs : dict
            Keyword arguments
        """
        super().__init__(scope, construct_id, **kwargs)

        self.dependency_finder_lambda = lambda_.Function(
            self,
            "DependencyFinderLambda",
            function_name="dependency-finder-handler",
            code=code,
            handler="SDSCode.pipeline_lambdas.dependency.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            memory_size=512,
            timeout=Duration.minutes(1),
            layers=layers,
        )

        api.add_route(
            route="dependency",
            http_method="GET",
            lambda_function=self.dependency_finder_lambda,
        )
