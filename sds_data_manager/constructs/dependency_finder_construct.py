"""Configure the Dependency lambda and API."""

from aws_cdk import Duration
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secrets
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
        vpc,
        rds_security_group,
        db_secret_name,
        env,
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
        vpc : obj
            The VPC
        rds_security_group : obj
            The RDS security group
        db_secret_name : str
            The DB secret name
        env : obj
            The CDK environment
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
            vpc=vpc,
            security_groups=[rds_security_group],
            environment={
                "REGION": env.region,
                "SECRET_NAME": db_secret_name,
            },
        )

        api.add_route(
            route="dependency",
            http_method="GET",
            lambda_function=self.dependency_finder_lambda,
        )

        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", db_secret_name
        )
        rds_secret.grant_read(grantee=self.dependency_finder_lambda)
