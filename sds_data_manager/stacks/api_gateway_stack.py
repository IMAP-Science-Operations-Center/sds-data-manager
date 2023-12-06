"""API Gateway Stack

Sets up api gateway, creates routes, and creates methods that
are linked to the lambda function.

An example of the format of the url:
https://api.prod.imap-mission.com/query
"""
from pathlib import Path
from typing import Optional

from aws_cdk import Duration, Stack, aws_sns
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_python_alpha as lambda_alpha_
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import aws_secretsmanager as secrets
from constructs import Construct

from sds_data_manager.stacks.domain_stack import DomainStack


class ApiGateway(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        domain_stack: DomainStack = None,
        **kwargs,
    ) -> None:
        """API Gateway Stack

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        domain_stack : DomainStack, Optional
            Custom domain, hosted zone, and certificate
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
                rest_api=self.api,
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
        self, route: str, http_method: str, lambda_function: lambda_.Function
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
        """
        # Define the API Gateway Resources
        resource = self.api.root.add_resource(route)

        # Create a new method that is linked to the Lambda function
        resource.add_method(http_method, apigw.LambdaIntegration(lambda_function))


class APILambda(Stack):
    """Generic Stack to create API handler Lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        lambda_name: str,
        code_path: Path,
        lambda_handler: str,
        timeout: Duration,
        rds_security_group: ec2.SecurityGroup,
        db_secret_name: str,
        vpc: ec2.Vpc,
        environment: Optional[dict] = None,
        **kwargs,
    ):
        """
        Lambda Constructor.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        lambda_name : str
            Lambda name
        code_path : Path
            Path to the Lambda code directory
        lambda_handler : str
            Lambda handler's function name
        timeout : Duration
            Lambda timeout
        rds_security_group : ec2.SecurityGroup
            RDS security group
        db_secret_name : str
            RDS secret name for secret manager access
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        environment: dict
            Lambda's environment variables.
        """

        super().__init__(scope, construct_id, **kwargs)

        self.lambda_function = lambda_alpha_.PythonFunction(
            self,
            id=lambda_name,
            function_name=lambda_name,
            entry=str(code_path.parent / "SDSCode"),  # This gives folder path
            index=str(code_path.name),  # This gives file name
            handler=lambda_handler,  # This points to function inside the file
            runtime=lambda_.Runtime.PYTHON_3_11,
            timeout=timeout,
            memory_size=512,
            environment=environment,
            vpc=vpc,
            security_groups=[rds_security_group],
            allow_public_subnet=True,
        )

        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", db_secret_name
        )
        rds_secret.grant_read(grantee=self.lambda_function)
