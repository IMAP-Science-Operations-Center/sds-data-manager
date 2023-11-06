from aws_cdk import Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from constructs import Construct

from sds_data_manager.stacks.domain_stack import DomainStack


class ApiGateway(Stack):
    """Sets up api gateway, creates routes, and creates methods that
    are linked to the lambda function.

    An example of the format of the url:
    https://api.tlcs-dev.imap-mission.com/query
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        lambda_functions: dict,
        domain_stack: DomainStack = None,
        **kwargs,
    ) -> None:
        """
        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        lambda_functions : dict
            Lambda functions
        domain_stack : DomainStack, Optional
            Custom domain, hosted zone, and certificate
        """
        super().__init__(scope, construct_id, **kwargs)

        # Define routes
        routes = lambda_functions.keys()

        # Create a single API Gateway
        api = apigw.RestApi(
            self,
            "RestApi",
            rest_api_name="RestApi",
            description="API Gateway for lambda function endpoints.",
            endpoint_types=[apigw.EndpointType.REGIONAL],
        )

        # Add a custom domain to the API if we have one
        if domain_stack is not None:
            custom_domain = apigw.DomainName(
                self,
                "RestAPI-DomainName",
                domain_name=f"api.{domain_stack.domain_name}",
                certificate=domain_stack.certificate,
                endpoint_type=apigw.EndpointType.REGIONAL,
            )

            # Route domain to api gateway
            apigw.BasePathMapping(
                self,
                "RestAPI-BasePathMapping",
                domain_name=custom_domain,
                rest_api=api,
            )

            # Add record to Route53
            route53.ARecord(
                self,
                "RestAPI-AliasRecord",
                zone=domain_stack.hosted_zone,
                record_name=f"api.{domain_stack.domain_name}",
                target=route53.RecordTarget.from_alias(
                    targets.ApiGatewayDomain(custom_domain)
                ),
            )

        # Loop through the lambda functions to create resources (routes)
        for route in routes:
            # Get the lambda function and its HTTP method
            lambda_info = lambda_functions[route]
            lambda_fn = lambda_info["function"]
            http_method = lambda_info["httpMethod"]

            # Define the API Gateway Resources
            resource = api.root.add_resource(route)

            # Create a new method that is linked to the Lambda function
            resource.add_method(http_method, apigw.LambdaIntegration(lambda_fn))
