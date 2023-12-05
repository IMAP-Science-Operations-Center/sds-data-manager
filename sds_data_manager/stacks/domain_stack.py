# Installed
from aws_cdk import Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_route53 as route53
from constructs import Construct


class DomainStack(Stack):
    """Acquires hosted_zone and certificate

    NOTE: Please make sure domain_name is registered
    in AWS account. This step is manual. And
    follow rest of manual setup steps documented in
    <doc path>.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        account_name: str,
        domain_name: str,
        **kwargs,
    ) -> None:
        """
        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        account_name : str
            Account name (e.g. dev)
        domain_name : str
            Domain name (e.g. imap-mission.com)
        """
        super().__init__(scope, construct_id, **kwargs)
        # Use the base domain name for the hosted zone lookup (imap-mission.com)
        self.hosted_zone = route53.HostedZone.from_lookup(
            self, "HostedZone", domain_name=domain_name
        )
        # Everywhere else we want the subdomain (dev.imap-mission.com)
        self.domain_name = f"{account_name}.{domain_name}"

        self.certificate = acm.Certificate(
            self,
            "Certificate-base",
            domain_name=f"*.{self.domain_name}",
            validation=acm.CertificateValidation.from_dns(self.hosted_zone),
        )
