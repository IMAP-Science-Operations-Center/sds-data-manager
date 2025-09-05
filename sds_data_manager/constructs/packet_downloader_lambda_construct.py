"""Configure packet downloading lambda."""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_notifications as s3n
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct
from imap_data_access import SPICEFilePath


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

        # Notify the lambda whenever a new file matching our repoint filename is added
        data_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(packet_lambda),  # Lambda notification
            s3.NotificationKeyFilter(
                prefix=f"{SPICEFilePath._dir_prefix}/repoint/imap_",
                suffix=".repoint.csv",
            ),
        )
