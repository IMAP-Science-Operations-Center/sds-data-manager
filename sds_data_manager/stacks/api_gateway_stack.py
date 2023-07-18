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

    def __init__(self, scope: Construct,
                 construct_id: str,
                 sds_id: str,
                 lambda_functions: dict,
                 env: Environment,
                 hosted_zone: route53.IHostedZone = None,
                 certificate: acm.ICertificate = None,
                 use_custom_domain: bool = False,
                 environment_name: str = "dev",
                 **kwargs) -> None:

        super().__init__(scope, construct_id, env=env, **kwargs)

        # Define subdomains
        subdomains = lambda_functions.keys()

        # Create a single API Gateway
        api = apigw.RestApi(self, f'api-RestApi-{sds_id}',
                            rest_api_name=f'My Service',
                            description=f'This service serves as my API Gateway.',
                            deploy_options=apigw.StageOptions(stage_name=f'{sds_id}'),
                            endpoint_types=[apigw.EndpointType.REGIONAL]
                            )

        # Define a custom domain
        if use_custom_domain:
            custom_domain = apigw.DomainName(self,
                                             f'api-DomainName-{sds_id}',
                                             domain_name=f'api.{environment_name}.imap-mission.com',
                                             certificate=certificate,
                                             endpoint_type=apigw.EndpointType.REGIONAL
                                             )

            # Route domain to api gateway
            apigw.BasePathMapping(self, f'api-BasePathMapping-{sds_id}',
                                  domain_name=custom_domain,
                                  rest_api=api,
                                  )

            # Add record to Route53
            route53.ARecord(self, f'api-AliasRecord-{sds_id}',
                            zone=hosted_zone,
                            record_name=f'api.{environment_name}.imap-mission.com',
                            target=route53.RecordTarget.from_alias(targets.ApiGatewayDomain(custom_domain))
                            )

        # Loop through the lambda functions to create resources (routes) in the API Gateway
        for subdomain in subdomains:
            # Get the lambda function and its HTTP method
            lambda_info = lambda_functions[subdomain]
            lambda_fn = lambda_info['function']
            http_method = lambda_info['httpMethod']

            # Define the API Gateway Resources
            resource = api.root.add_resource(subdomain)

            # Create a new method that is linked to the Lambda function
            resource.add_method(http_method, apigw.LambdaIntegration(lambda_fn))
