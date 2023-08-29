# Installed
from aws_cdk import Environment, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_iam as iam
from aws_cdk import aws_route53 as route53
from constructs import Construct


class Domain(Stack):
    """Acquires hosted_zone and certificate"""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        sds_id: str,
        env: Environment,
        use_custom_domain: bool = False,
        **kwargs,
    ) -> None:
        """
        Parameters
        ----------
        scope : Construct
        construct_id : str
        sds_id : str
            Name suffix for stack
        env : Environment
        use_custom_domain : bool, Optional
            Build API Gateway using custom domain
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        self.route_53_role = iam.Role(
            self,
            f"Route53Role-{sds_id}",
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal("ec2.amazonaws.com")
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonRoute53FullAccess"
                )
            ],
        )

        if use_custom_domain:
            self.hosted_zone = route53.HostedZone.from_lookup(
                self, f"HostedZone-{sds_id}", domain_name="imap-mission.com"
            )

            self.certificate = acm.Certificate(
                self,
                f"Certificate-{sds_id}",
                domain_name=f"*.{sds_id}.imap-mission.com",
                validation=acm.CertificateValidation.from_dns(self.hosted_zone),
            )
        else:
            self.hosted_zone = None
            self.certificate = None
