from aws_cdk import Environment, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from constructs import Construct


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
        sds_id: str,
        lambda_functions: dict,
        env: Environment,
        hosted_zone: route53.IHostedZone = None,
        certificate: acm.ICertificate = None,
        use_custom_domain: bool = False,
        **kwargs,
    ) -> None:
        """
        Parameters
        ----------
        scope : Construct
        construct_id : str
        sds_id : str
            Name suffix for stack
        lambda_functions : dict
            Lambda functions
        env : Environment
        hosted_zone : route53.IHostedZone
            Hosted zone used for DNS routing.
        certificate : acm.ICertificate
            Used for validating the secure connections to API Gateway.
        use_custom_domain : bool
            Use if account contains registered domain.
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        # Define routes
        routes = lambda_functions.keys()

        # Create a single API Gateway
        api = apigw.RestApi(
            self,
            f"api-RestApi-{sds_id}",
            rest_api_name=f"api-RestApi-{sds_id}",
            description="API Gateway for lambda function endpoints.",
            deploy_options=apigw.StageOptions(stage_name=f"{sds_id}"),
            endpoint_types=[apigw.EndpointType.REGIONAL],
        )

        # Define a custom domain
        if use_custom_domain:
            custom_domain = apigw.DomainName(
                self,
                f"api-DomainName-{sds_id}",
                domain_name=f"api.{sds_id}.imap-mission.com",
                certificate=certificate,
                endpoint_type=apigw.EndpointType.REGIONAL,
            )

            # Route domain to api gateway
            apigw.BasePathMapping(
                self,
                f"api-BasePathMapping-{sds_id}",
                domain_name=custom_domain,
                rest_api=api,
            )

            # Add record to Route53
            route53.ARecord(
                self,
                f"api-AliasRecord-{sds_id}",
                zone=hosted_zone,
                record_name=f"api.{sds_id}.imap-mission.com",
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
