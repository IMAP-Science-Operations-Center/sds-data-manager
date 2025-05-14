"""Configure the API Gateway Construct.

Sets up api gateway, creates routes, and creates methods that are linked to the
lambda function.

An example of the format of the url: https://api.prod.imap-mission.com/query
https://ialirt.prod.imap-mission.com/ialirt-log-query
"""

from typing import Optional

from aws_cdk import Duration, aws_sns
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_authorizers as apigwv2_authorizers
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from sds_data_manager.constructs.route53_hosted_zone import DomainConstruct


class ApiGateway(Construct):
    """Construct for creating an API Gateway."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        domain_construct: Optional[DomainConstruct] = None,
        certificate: Optional[acm.Certificate] = None,
        ialirt_prefix: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Construct the API Gateway Construct.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        domain_construct : DomainConstruct, Optional
            Custom domain, hosted zone
        certificate : Certificate, Optional
            SSL certificate for the custom domain (in the same region)
        ialirt_prefix : str
            Prefix for ialirt domain, Optional
        kwargs : dict
            Keyword arguments
        """
        super().__init__(scope, construct_id, **kwargs)

        if ialirt_prefix is not None:
            self.prefix = ialirt_prefix
            self.lowercase_prefix = f"{ialirt_prefix.lower()}"
        else:
            self.prefix = ""
            self.lowercase_prefix = "api"

        # Start with an empty domain name mapping and fill it in within
        # the domain construct if necessary within the lower if-block
        domain_mapping = None

        # NOTE: We look these up from the account parameter store. To update
        # these values, run the following command:
        #
        # aws ssm put-parameter --name lasp-auth-issuer --value <issuer> --type String
        #
        # where <issuer>, <audience>, and <scope> are values retrieved from the
        # LASP Web Team.

        self.auth_issuer = ssm.StringParameter.from_string_parameter_name(
            scope=scope, id="SSMAuthIssuer", string_parameter_name="lasp-auth-issuer"
        ).string_value
        self.auth_audience = ssm.StringParameter.from_string_parameter_name(
            scope=scope,
            id="SSMAuthAudience",
            string_parameter_name="lasp-auth-audience",
        ).string_value
        self.auth_scope = ssm.StringParameter.from_string_parameter_name(
            scope=scope, id="SSMAuthScope", string_parameter_name="lasp-auth-scope"
        ).string_value

        self.authorizer = apigwv2_authorizers.HttpJwtAuthorizer(
            id=f"{self.lowercase_prefix}JwtAuthorizer",
            jwt_issuer=self.auth_issuer,
            jwt_audience=[self.auth_audience],
        )

        # Add a custom domain to the API if we have one
        if domain_construct is not None:
            api_domain_name = f"{self.lowercase_prefix}.{domain_construct.domain_name}"

            custom_domain = apigwv2.DomainName(
                self,
                f"{self.lowercase_prefix}HttpAPI-DomainName",
                domain_name=api_domain_name,
                certificate=certificate,
            )
            # Create a domain mapping for the API that can be used later for the
            # custom domain mapping in the default stage
            domain_mapping = {"domain_name": custom_domain}

            # Add record to Route53
            route53.ARecord(
                self,
                f"{self.prefix}HttpAPI-AliasRecord",
                zone=domain_construct.hosted_zone,
                record_name=api_domain_name,
                target=route53.RecordTarget.from_alias(
                    targets.ApiGatewayv2DomainProperties(
                        regional_domain_name=custom_domain.regional_domain_name,
                        regional_hosted_zone_id=custom_domain.regional_hosted_zone_id,
                    )
                ),
            )

        # Create a single HTTP API Gateway
        self.api = apigwv2.HttpApi(
            self,
            f"{self.lowercase_prefix}HttpApi",
            api_name=f"{self.prefix}HttpApi",
            default_domain_mapping=domain_mapping,
            description="HTTP API Gateway for lambda function endpoints.",
            cors_preflight={
                "allow_origins": ["*"],
                "allow_methods": [
                    apigwv2.CorsHttpMethod.GET,
                    apigwv2.CorsHttpMethod.POST,
                    apigwv2.CorsHttpMethod.OPTIONS,
                ],
            },
        )

    def deliver_to_sns(self, sns_topic: aws_sns.Topic):
        """Deliver API Gateway alerts to an SNS topic.

        Creates cloudwatch metrics to monitor resources and sends
        alerts to the SNS topic if any of the metrics are breached.

        Parameters
        ----------
        sns_topic : aws_sns.Topic
            SNS Topic to send any API alerts to.

        """
        # Define the metric the alarm is based on
        # List of Metric options for API Gateway:
        # https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-metrics-and-dimensions.html
        metric = self.api.metric_latency(
            period=Duration.minutes(1),
            statistic="Maximum",
            label="API Gateway Latency",
        )

        # Define the alarm
        cloudwatch_alarm = cloudwatch.Alarm(
            self,
            f"{self.lowercase_prefix}gw-cw-alarm",
            alarm_name=f"{self.lowercase_prefix}gw-cw-alarm",
            alarm_description="API Gateway latency is high",
            actions_enabled=True,
            metric=metric,
            # Evaluate the metric over the past 60 minutes
            # alarming if any single datapoint is over the threshold
            # This will limit the alarm to once/hour
            evaluation_periods=60,
            datapoints_to_alarm=1,
            # If the maximum latency is greater than 10 seconds, send a notification
            threshold=10 * 1000,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        # Send notification to the SNS Topic
        cloudwatch_alarm.add_alarm_action(cloudwatch_actions.SnsAction(sns_topic))

    def add_route(
        self,
        route: str,
        http_method: str,
        lambda_function: lambda_.Function,
    ):
        """Add a route to the HTTP API Gateway.

        Parameters
        ----------
        route : str
            Route name. Eg. /download, /query, /upload, etc.
        http_method : str
            HTTP method. Eg. GET, POST, etc.
        lambda_function : lambda_.Function
            Lambda function to trigger when this route is hit.
        """
        # Add the authorizer to the route if it is a route that requires authentication
        authorizer = self.authorizer if route.startswith("/auth") else None
        authorization_scopes = [self.auth_scope] if route.startswith("/auth") else None
        # Add the route to the HTTP API
        self.api.add_routes(
            path=route,
            methods=[apigwv2.HttpMethod[http_method]],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                f"{self.prefix}-{route}-Integration", lambda_function
            ),
            authorizer=authorizer,
            authorization_scopes=authorization_scopes,
        )
