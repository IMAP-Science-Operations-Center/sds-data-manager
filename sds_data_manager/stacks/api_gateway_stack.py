# Installed
from constructs import Construct
from aws_cdk import (
    Stack,
    Environment,
    aws_apigateway as apigw,
    aws_route53 as route53,
    aws_certificatemanager as acm,
    aws_route53_targets as targets
)


class ApiGateway(Stack):
    """Sets up api gateway, creates subdomains, and creates methods that
    are linked to the lambda function"""
    #TODO: may add Edge-optimized APIs for production. We are using Regional for development.
    # (improve performance for users far from AWS Region where API is hosted.)

    def __init__(self, scope: Construct,
                 construct_id: str,
                 sds_id: str,
                 lambda_functions: dict,
                 hosted_zone: route53.IHostedZone,
                 certificate: acm.ICertificate,
                 env: Environment,
                 **kwargs) -> None:
        """
        Parameters
        ----------
        scope : Construct
        construct_id : str
        sds_id : str
            Name suffix for stack
        lambda_functions : dict
            Lambda functions
        hosted_zone : route53.IHostedZone
            Hosted zone used for DNS routing.
        certificate : acm.ICertificate
            Used for validating the secure connections to API Gateway.
        env : Environment
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        # Define subdomains
        subdomains = lambda_functions.keys()

        for subdomain in subdomains:
            # Set up the API Gateway
            api = apigw.RestApi(self, f'{subdomain}-RestApi-{sds_id}',
                                rest_api_name=f'My {subdomain.capitalize()} Service',
                                description=f'This service serves as my {subdomain.capitalize()} API Gateway.',
                                deploy_options=apigw.StageOptions(stage_name='dev'),
                                endpoint_types=[apigw.EndpointType.REGIONAL]
                                )

            # Define a custom domain
            custom_domain = apigw.DomainName(self,
                                             f'{subdomain}-DomainName-{sds_id}',
                                             domain_name=f'{subdomain}.imap-mission.com',
                                             certificate=certificate,
                                             endpoint_type=apigw.EndpointType.REGIONAL
                                             )

            # Route domain to api gateway
            apigw.BasePathMapping(self, f'{subdomain}-BasePathMapping-{sds_id}',
                                  domain_name=custom_domain,
                                  rest_api=api,
                                  )

            # Add record to Route53
            route53.ARecord(self, f'{subdomain}-AliasRecord-{sds_id}',
                            zone=hosted_zone,
                            record_name=f'{subdomain}.imap-mission.com',
                            target=route53.RecordTarget.from_alias(targets.ApiGatewayDomain(custom_domain))
                            )

            # Get the lambda function and its HTTP method
            lambda_info = lambda_functions[subdomain]
            lambda_fn = lambda_info['function']
            http_method = lambda_info['httpMethod']

            # Define the API Gateway Resources
            resource = api.root.add_resource(subdomain)

            # Create a new method that is linked to the Lambda function
            resource.add_method(http_method, apigw.LambdaIntegration(lambda_fn))
