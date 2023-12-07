"""NetworkingStack Stack"""
# Installed
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


# TODO: May not need everything here, but left it for now
class NetworkingStack(Stack):
    """General purpose networking components"""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs,
    ) -> None:
        """NetworkingStack constructor

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        """
        super().__init__(scope, construct_id, **kwargs)
        self.vpc = ec2.Vpc(
            self,
            "VPC",
            gateway_endpoints={
                "s3": ec2.GatewayVpcEndpointOptions(
                    service=ec2.GatewayVpcEndpointAwsService.S3
                )
            },
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="PublicVPC", subnet_type=ec2.SubnetType.PUBLIC
                ),
                ec2.SubnetConfiguration(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    name="PrivateVPC",
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    name="IsolatedVPC",
                    cidr_mask=24,
                ),
            ],
        )

        # Create security group for the RDS instance
        self.rds_security_group = ec2.SecurityGroup(
            self, "RdsSecurityGroup", vpc=self.vpc, allow_all_outbound=True
        )

        # Setup a security group for the Fargate-generated EC2 instances.
        self.batch_security_group = ec2.SecurityGroup(
            self, "FargateInstanceSecurityGroup", vpc=self.vpc
        )

        # The lambda is in the same private security group as the RDS, but
        # it needs to access the secrets manager, so we add this endpoint.
        self.vpc.add_interface_endpoint(
            "SecretManagerEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            private_dns_enabled=True,
        )

        self.rds_security_group.add_ingress_rule(
            self.batch_security_group, ec2.Port.tcp(5432), "Access from Fargate Batch"
        )
