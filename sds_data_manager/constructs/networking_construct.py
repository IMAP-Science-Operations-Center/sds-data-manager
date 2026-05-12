"""Configure the networking components."""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


# TODO: May not need everything here, but left it for now
class NetworkingConstruct(Construct):
    """General purpose networking components."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs,
    ) -> None:
        """NetworkingConstruct constructor.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        kwargs : dict
            Keyword arguments

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

        # Create the Virtual Private Gateway (VGW).
        # The VGW decrypts incoming IPSec packets from NOAA and hands them into the VPC.
        self.vpn_gateway = self._create_vpn_gateway()

    def _create_vpn_gateway(self) -> ec2.CfnVPNGateway:
        """Create a Virtual Private Gateway and attach it to the VPC."""
        # Create the Virtual Private Gateway (VGW).
        vpn_gateway = ec2.CfnVPNGateway(
            self,
            "VpnGateway",
            # IPSec version 1 is the standard protocol for encrypted VPN tunnels.
            type="ipsec.1",
            tags=[cdk.CfnTag(key="Name", value="ialirt-vpn-gateway")],
        )

        # Attach the VGW to the VPC so decrypted traffic can enter the VPC.
        ec2.CfnVPCGatewayAttachment(
            self,
            "VpnGatewayAttachment",
            vpc_id=self.vpc.vpc_id,
            vpn_gateway_id=vpn_gateway.ref,
        )

        # Adds VPN route propagation to each public subnet so that regardless of
        # which AZ the I-ALiRT EC2 lands in, it can receive traffic from the VPN.
        for i, subnet in enumerate(self.vpc.public_subnets):
            ec2.CfnVPNGatewayRoutePropagation(
                self,
                f"RoutePropagate{i}",
                route_table_ids=[subnet.route_table.route_table_id],
                vpn_gateway_id=vpn_gateway.ref,
            )

        return vpn_gateway
