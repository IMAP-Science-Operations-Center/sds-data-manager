"""Configure packet downloading lambda."""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_events
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_ssm as ssm
from constructs import Construct


class PacketDownloaderLambda(Construct):
    """Construct for packet downloading lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        code: lambda_.Code,
        layers: list,
        data_bucket: s3.Bucket,
        vpc: ec2.Vpc,
        data_access_url: str = "",
        **kwargs,
    ) -> None:
        """MonitoringLambda Construct.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        code : aws_lambda.Code
            Lambda code bundle
        layers : list
            List of Lambda layers for this function to use
        data_bucket : s3.Bucket
            The S3 bucket to which the lambda will listen for events
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        data_access_url : str
            The data access URL to use for this job, by default the empty string.
            You should set this to the appropriate API endpoint, e.g.
            https://api.dev.imap-mission.com
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        packet_lambda = lambda_.Function(
            self,
            id="PacketDownloaderLambda",
            function_name="packet-downloader",
            code=code,
            handler="SDSCode.pipeline_lambdas.packet_downloader.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            layers=layers,
            vpc=vpc,
            # We need a subnet with internet egress access to download data from webpoda
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            # We are downloading data, so make sure we have a longer timeout here
            timeout=cdk.Duration.minutes(15),
            memory_size=2048,
            # Environment variables - API key will be retrieved from SSM at runtime
            environment={
                "IMAP_DATA_ACCESS_URL": data_access_url,
                "SSM_API_KEY_PARAMETER": "/imap-sdc/batch-jobs/api-key",
                "S3_BUCKET": data_bucket.bucket_name,
            },
        )

        # Allow the function to read/write from our bucket
        data_bucket.grant_read_write(packet_lambda)

        # Reference an existing Secrets Manager secret
        # NOTE: This secret must be created in the same region as the lambda
        #       and the lambda must have permissions to read it
        #       The secret is given to us by the webpoda team, and has to be
        #       updated manually when it changes.
        # aws secretsmanager create-secret --name webpoda-token --secret-string ABC123
        secret = secretsmanager.Secret.from_secret_name_v2(
            self, "WebpodaSecret", "webpoda-token"
        )
        secret.grant_read(packet_lambda)

        # Grant permission to read the API key from SSM Parameter Store
        api_key_parameter = ssm.StringParameter.from_secure_string_parameter_attributes(
            self,
            "ApiKeyParameter",
            parameter_name="/imap-sdc/batch-jobs/api-key",
        )
        api_key_parameter.grant_read(packet_lambda)

        # Trigger the lambda on a schedule (every 6 hours)
        rule = aws_events.Rule(
            self,
            "PacketDownloaderScheduleRule",
            # Every 6 hours at 5 minutes past the hour
            # 00:00 -> 06:00, 06:00 -> 12:00, 12:00 -> 18:00, 18:00 -> 00:00
            schedule=cdk.aws_events.Schedule.cron(minute="5", hour="*/6"),
        )
        rule.add_target(cdk.aws_events_targets.LambdaFunction(packet_lambda))
