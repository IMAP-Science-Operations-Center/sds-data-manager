"""Configure the API Gateway Construct.

Sets up api gateway, creates routes, and creates methods that are linked to the
lambda function.

An example of the format of the url: https://api.prod.imap-mission.com/query
"""

from aws_cdk import Duration, aws_sns
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from constructs import Construct

from sds_data_manager.constructs.route53_hosted_zone import DomainConstruct


class ApiGateway(Construct):
    """Construct for creating an API Gateway."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        domain_construct: DomainConstruct = None,
        certificate: acm.Certificate = None,
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
        certificate : Certificate
            SSL certificate for the custom domain (in the same region)
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        # Create a single API Gateway
        self.api = apigw.RestApi(
            self,
            "RestApi",
            rest_api_name="RestApi",
            description="API Gateway for lambda function endpoints.",
            endpoint_types=[apigw.EndpointType.REGIONAL],
        )

        # Add a custom domain to the API if we have one
        if domain_construct is not None:
            custom_domain = apigw.DomainName(
                self,
                "RestAPI-DomainName",
                domain_name=f"api.{domain_construct.domain_name}",
                certificate=certificate,
                endpoint_type=apigw.EndpointType.REGIONAL,
            )

            # Route domain to api gateway
            apigw.BasePathMapping(
                self,
                "RestAPI-BasePathMapping",
                domain_name=custom_domain,
                rest_api=self.api,
            )

            # Add record to Route53
            route53.ARecord(
                self,
                "RestAPI-AliasRecord",
                zone=domain_construct.hosted_zone,
                record_name=f"api.{domain_construct.domain_name}",
                target=route53.RecordTarget.from_alias(
                    targets.ApiGatewayDomain(custom_domain)
                ),
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
        metric = cloudwatch.Metric(
            namespace="AWS/ApiGateway",
            metric_name="Latency",
            dimensions_map={"ApiName": self.api.rest_api_name},
            period=Duration.minutes(1),
            statistic="Maximum",
        )

        # Define the alarm
        cloudwatch_alarm = cloudwatch.Alarm(
            self,
            "apigw-cw-alarm",
            alarm_name="apigw-cw-alarm",
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
        use_path_params: bool = False,
    ):
        """Add a route to the API Gateway.

        Parameters
        ----------
        route : str
            Route name. Eg. /download, /query, /upload, etc.
        http_method : str
            HTTP method. Eg. GET, POST, etc.
        lambda_function : lambda_.Function
            Lambda function to trigger when this route is hit.
        use_path_params : bool, optional
            Whether or not to use path parameters, by default False
            This allows for ``/download/{filename}`` style routes.

        """
        # Define the API Gateway Resources
        resource = self.api.root.add_resource(route)
        if use_path_params:
            # Need to add a proxy resource to allow path parameters
            resource = resource.add_proxy(any_method=False)

        # Create a new method that is linked to the Lambda function
        resource.add_method(http_method, apigw.LambdaIntegration(lambda_function))
