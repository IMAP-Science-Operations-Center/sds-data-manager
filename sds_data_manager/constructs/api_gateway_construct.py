"""Configure the API Gateway Construct.

Sets up api gateway, creates routes, and creates methods that are linked to the
lambda function.

An example of the format of the url: https://api.prod.imap-mission.com/query
https://ialirt.prod.imap-mission.com/ialirt-log-query
"""

from typing import Optional

from aws_cdk import Duration, RemovalPolicy, aws_sns
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_authorizers as apigwv2_authorizers
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_dynamodb as dynamodb
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
        create_api_keys_table: bool = True,
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
        create_api_keys_table : bool
            Whether to create a new API keys table or reference existing one
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

        # Lambda function for API Key authorizer
        self.api_key_authorizer_lambda = lambda_.Function(
            self,
            f"{self.lowercase_prefix}ApiKeyAuthorizerLambda",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="lambda_api_key_authorizer.lambda_handler",
            code=lambda_.Code.from_asset("sds_data_manager/lambda_code/authorization"),
            timeout=Duration.seconds(10),
        )

        # Create or reference the API Keys DynamoDB table
        if create_api_keys_table:
            self.api_keys_table = self._create_api_keys_table()
        else:
            # Reference existing table by name
            self.api_keys_table = dynamodb.Table.from_table_name(
                self, "ExistingApiKeysTable", table_name="imap-sdc-api-keys"
            )

        # Grant the API key authorizer lambda permission to read from DynamoDB
        self.api_keys_table.grant_read_data(self.api_key_authorizer_lambda)

        self.api_key_authorizer = apigwv2_authorizers.HttpLambdaAuthorizer(
            id=f"{self.lowercase_prefix}ApiKeyAuthorizer",
            handler=self.api_key_authorizer_lambda,
            response_types=[apigwv2_authorizers.HttpLambdaResponseType.SIMPLE],
            identity_source=["$request.header.x-api-key"],
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
                "allow_headers": ["*"],
                "allow_origins": ["*"],
                "allow_methods": [apigwv2.CorsHttpMethod.ANY],
            },
        )

    def _create_api_keys_table(self) -> dynamodb.Table:
        """Create the DynamoDB table for API keys.

        The partion key is the api_key to enable O(1) lookups of API keys for
        quick verification in the lambda authorizer.

        Returns
        -------
        dynamodb.Table
            The created DynamoDB table for API keys
        """
        return dynamodb.Table(
            self,
            "ApiKeysTable",
            table_name="imap-sdc-api-keys",
            partition_key=dynamodb.Attribute(
                name="api_key", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
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

        If the route begins with /authorized, use the JWT authorizer.
        If the route beings with /api-key, use the API Key authorizer.

        Parameters
        ----------
        route : str
            Route name. Eg. /download, /query, /upload, etc.
        http_method : str
            HTTP method. Eg. GET, POST, etc.
        lambda_function : lambda_.Function
            Lambda function to trigger when this route is hit.
        """
        # normalize root route
        if route in ["", "/"]:
            route = "/"

        # Add the authorizer to the route if it is a route that requires authentication
        authorizer = None
        authorization_scopes = None
        if route.startswith("/api-key"):
            authorizer = self.api_key_authorizer
        elif route.startswith("/authorized"):
            authorizer = self.authorizer
            authorization_scopes = [self.auth_scope]

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
