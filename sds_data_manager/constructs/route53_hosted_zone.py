"""Set up domain name routing and authorization."""

from pathlib import Path

import boto3
from aws_cdk import CfnOutput, Fn
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk.aws_lambda import FunctionUrl
from constructs import Construct


class DomainConstruct(Construct):
    """Construct that manages the Hosted Zone and SSL Certificate for an apex domain.

    Sample curl command:
        curl -X POST -H "Content-Type: application/json" -d '{"ns_values":
        ["<sample NS>.net.", "<sample NS>.com.", "<sample NS>.org.",
        "<sample NS>.co.uk."], "subdomain": "<subdomain>"}'
        https://authorize-subdomains.<apex domain name>
    Note: A sample curl command will be output after running 'cdk deploy'.
    Fill in the NS records and the subdomain name then run the command to update
    the apex hosted zone. For more info on setup, usage, and troubleshooting:
    https://confluence.lasp.colorado.edu/display/MODSDB/IMAP+Subdomain+Creation+Stack+Instructions
    run 'whois domain_name|grep -i status' to see status of ssl cert approval
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        domain_name: str,
        create_new_hosted_zone: bool,
        **kwargs,
    ) -> None:
        """Create the domain cronstruct for handling the Apex Domain.

        Parameters
        ----------
        scope : Construct
            The parent construct of this construct.
        id : str
            The ID of this construct.
        domain_name : str
            The name of the domain. (imap-mission.com, dev.imap-mission.com, ...)
        create_new_hosted_zone : bool
            Whether to create a new hosted zone for the apex domain.
        **kwargs : dict
            Additional keyword arguments.
        """
        super().__init__(scope, id, **kwargs)
        self.domain_name = domain_name

        if create_new_hosted_zone:
            # Creating new hosted zone for apex domain
            self.hosted_zone = route53.HostedZone(
                self, "DomainHostedZone", zone_name=domain_name
            )

            # creating new certificate for *.<domain_name>
            self.certificate = acm.Certificate(
                self,
                "DomainCertificate",
                domain_name=f"*.{domain_name}",  # *.imap-mission.com
                subject_alternative_names=[domain_name],  # imap-mission.com
                validation=acm.CertificateValidation.from_dns(
                    hosted_zone=self.hosted_zone
                ),
            )
        else:
            # Getting the hosted zone ID
            self.hosted_zone = route53.HostedZone.from_lookup(
                self, "DomainHostedZone", domain_name=domain_name
            )

            # importing existing ssl certificate
            self.certificate = acm.Certificate.from_certificate_arn(
                self,
                "DomainCertificate",
                certificate_arn=self.get_ssl_cert_arn(f"*.{domain_name}"),
            )

    def setup_cf_and_lambda_authorizer(self, allowed_ip: str) -> None:
        """Set up the CloudFront distribution and Lambda authorizer.

        This sets up a CF Distribution that points to a Lambda function that
        authorizes requests to update the apex domain. For example, we want to
        authorize "dev.imap-mission.com" to be created from a different account.

        Parameters
        ----------
        allowed_ip : str
            The IP address that is allowed to update the apex domain.
        """
        # Define the Lambda function
        code_directory = str(
            Path(__file__).parent.parent / "lambda_code/route53_hosted_zone_code"
        )
        lambda_function = _lambda.Function(
            self,
            "UpdateR53Lambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_handler.lambda_handler",
            code=_lambda.Code.from_asset(code_directory),
        )
        # Adding environmental variables to the lambda
        lambda_function.add_environment(
            "hosted_zone_id", self.hosted_zone.hosted_zone_id
        )
        lambda_function.add_environment("allowed_ip", allowed_ip)
        lambda_function.add_environment("apex_domain_name", self.domain_name)

        # Creating lambda URL
        lambda_function_url = FunctionUrl(
            self,
            "UpdateR53LambdaFunctionUrl",
            function=lambda_function,
            auth_type=_lambda.FunctionUrlAuthType.NONE,
        )
        # Addding invoke permission to the lambda URL
        lambda_function_url.grant_invoke_url(iam.AnyPrincipal())

        # Remove the 'https://' from the Lambda URL
        lambda_url_no_protocol = Fn.select(
            1, Fn.split("https://", lambda_function_url.url)
        )
        # Remove any trailing slashes
        cleaned_lambda_url = Fn.select(0, Fn.split("/", lambda_url_no_protocol))

        # Grant the Lambda function permissions to update/list records in Route 53
        route53_policy = iam.PolicyStatement(
            actions=[
                "route53:ChangeResourceRecordSets",
                "route53:ListResourceRecordSets",
            ],
            resources=[
                f"arn:aws:route53:::hostedzone/{self.hosted_zone.hosted_zone_id}"
            ],
        )
        lambda_function.add_to_role_policy(route53_policy)

        # Create a cloud front distribution (used for creating a custom lambda URL)
        cloudfront_dist = cloudfront.Distribution(
            self,
            "customLambdaURLCFDist",
            default_behavior=cloudfront.BehaviorOptions(
                # cloud front does not allow 'http://' or trailing slashes in the origin
                origin=origins.HttpOrigin(cleaned_lambda_url),
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            enabled=True,
            # USA, Canada, Europe, & Israel (cheapest option)
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            http_version=cloudfront.HttpVersion.HTTP2,
            # Adds CNAME record
            domain_names=[f"authorize-subdomains.{self.domain_name}"],
            certificate=self.certificate,  # SSL Certificate
            default_root_object=None,
            enable_logging=False,
        )

        # Adds an A record to the apex hosted zone pointing to the
        # cloud front distibution
        route53.ARecord(
            self,
            "ARecordLambdaURL",
            zone=self.hosted_zone,
            record_name="authorize-subdomains",
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(cloudfront_dist)
            ),
        )

        sample_curl = (
            f'curl -X POST -H "Content-Type: application/json" -d '
            f'\'{{"ns_values": ["<sample NS>.net.", "<sample NS>.com.", '
            f'"<sample NS>.org.", "<sample NS>.co.uk."], '
            f'"subdomain": "<subdomain>"}}\' '
            f"https://authorize-subdomains.{self.domain_name}"
        )
        CfnOutput(
            self,
            "Sample curl Command",
            value=sample_curl,
        )

    def get_ssl_cert_arn(self, domain: str) -> str:
        """Find an SSL certificate by domain name.

        Parameters
        ----------
        domain : str
            The domain name of the SSL certificate to find.
        """
        acm_client = boto3.client("acm")
        res = acm_client.list_certificates(CertificateStatuses=["ISSUED"])

        for cert in res["CertificateSummaryList"]:
            if cert["DomainName"] == domain:
                return cert["CertificateArn"]

        raise ValueError(f"No certificate found for domain name: {domain}")
