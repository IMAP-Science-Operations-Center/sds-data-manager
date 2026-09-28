"""Helpers for building I-ALiRT's NOAA Site-to-Site VPN path."""

from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_ssm as ssm
from aws_cdk import custom_resources as cr

from sds_data_manager.constructs import ialirt_vpn_construct, networking_construct


def build_noaa_vpn_tgw(
    ialirt_stack: Stack,
    networking: networking_construct.NetworkingConstruct,
) -> None:
    """Build the NOAA VPN Transit Gateway path for I-ALiRT.

    Terminates NOAA's Site-to-Site VPN on a Transit Gateway attached to
    I-ALiRT's VPC, so decrypted traffic reaches I-ALiRT via the existing
    NAT Gateway / Elastic IP path.

    Parameters
    ----------
    ialirt_stack : Stack
        The I-ALiRT stack to parent these resources to.
    networking : networking_construct.NetworkingConstruct
        Networking construct providing the VPC to attach to the TGW.

    """
    # Retrieve the NOAA VPN pre-shared key from Secrets Manager.
    # Store the PSK under the key "psk" in a secret named
    # "/ialirt/noaa/noaa-vpn-psk" before deploying this stack.
    noaa_vpn_psk = (
        secretsmanager.Secret.from_secret_name_v2(
            ialirt_stack, "NoaaVpnPsk", "/ialirt/noaa/noaa-vpn-psk"
        )
        .secret_value_from_json("psk")
        .unsafe_unwrap()
    )

    # Retrieve NOAA's border router IPs from SSM Parameter Store.
    # Store these before deploying:
    #   aws ssm put-parameter --name "/ialirt/noaa-vpn/wash-ip"
    #   --value "<ip>" --type String
    #   aws ssm put-parameter --name "/ialirt/noaa-vpn/denv-ip"
    #   --value "<ip>" --type String
    noaa_wash_ip = ssm.StringParameter.value_for_string_parameter(
        ialirt_stack, "/ialirt/noaa-vpn/wash-ip"
    )
    noaa_denv_ip = ssm.StringParameter.value_for_string_parameter(
        ialirt_stack, "/ialirt/noaa-vpn/denv-ip"
    )

    ialirt_eip_ip = ssm.StringParameter.value_for_string_parameter(
        ialirt_stack, "/ialirt/noaa-vpn/eip-ip"
    )

    # Create a Transit Gateway (TGW) to terminate the IPSec tunnel from NOAA.
    ialirt_transit_gateway = ec2.CfnTransitGateway(
        ialirt_stack,
        "IalirtTransitGateway",
        # ASN is BGP's identifier for a distinct network/routing domain —
        # how two BGP peers identify themselves and each other.
        # Default ASN, but stated explicitly here.
        amazon_side_asn=64512,
        # Use our own route table below instead of TGW's hidden default one.
        default_route_table_association="disable",
        default_route_table_propagation="disable",
        description="I-ALiRT TGW for NOAA VPN",
    )

    # Attach the VPC to the TGW via the private subnets, whose route tables
    # already have 0.0.0.0/0 -> NAT Gateway — so traffic arriving via the TGW
    # attachment gets forwarded there automatically. The NAT Gateway then
    # sends it out to the internet, where it reaches I-ALiRT's own Elastic
    # IP the same way every other partner's traffic already does.
    ialirt_vpc_attachment = ec2.CfnTransitGatewayVpcAttachment(
        ialirt_stack,
        "IalirtTransitGatewayVpcAttachment",
        transit_gateway_id=ialirt_transit_gateway.attr_id,
        vpc_id=networking.vpc.vpc_id,
        subnet_ids=[subnet.subnet_id for subnet in networking.vpc.private_subnets],
    )

    # Log only traffic crossing the TGW VPC attachment itself, i.e. NOAA's
    # VPN traffic in/out of the VPC, rather than everything else the shared
    # private subnet carries (NAT Gateway egress, other resources' ENIs).
    # Satisfies the LASP monitoring responsibility (VPC Flow Logs, ACCEPT
    # and REJECT) documented for the I-ALiRT/NOAA interconnection.
    noaa_vpn_flow_log_group = logs.LogGroup(
        ialirt_stack,
        "IalirtVpnFlowLogs",
        retention=logs.RetentionDays.ONE_YEAR,
        removal_policy=RemovalPolicy.RETAIN,
    )
    # Note: traffic_type is not a supported parameter for Transit Gateway /
    # Transit Gateway Attachment flow logs (AWS always captures both ACCEPT
    # and REJECT records for this resource type).
    ec2.FlowLog(
        ialirt_stack,
        "IalirtVpnTgwAttachmentFlowLog",
        resource_type=ec2.FlowLogResourceType.from_transit_gateway_attachment_id(
            ialirt_vpc_attachment.attr_id
        ),
        destination=ec2.FlowLogDestination.to_cloud_watch_logs(noaa_vpn_flow_log_group),
    )

    ialirt_vpn = ialirt_vpn_construct.IalirtVpnConstruct(
        scope=ialirt_stack,
        construct_id="IalirtVpn",
        transit_gateway_id=ialirt_transit_gateway.attr_id,
        psk=noaa_vpn_psk,
        wash_ip=noaa_wash_ip,
        denv_ip=noaa_denv_ip,
    )

    # Create a TGW route table.
    ialirt_tgw_route_table = ec2.CfnTransitGatewayRouteTable(
        ialirt_stack,
        "IalirtTransitGatewayRouteTable",
        transit_gateway_id=ialirt_transit_gateway.attr_id,
    )
    tgw_route_table_id = ialirt_tgw_route_table.attr_transit_gateway_route_table_id

    # VPC's connection to the Transit Gateway will use our route table to
    # look up where to send traffic.
    ec2.CfnTransitGatewayRouteTableAssociation(
        ialirt_stack,
        "IalirtTgwRouteTableAssociationVpc",
        transit_gateway_attachment_id=ialirt_vpc_attachment.attr_id,
        transit_gateway_route_table_id=tgw_route_table_id,
    )

    # AWS::EC2::VPNConnection does not expose its Transit Gateway attachment
    # ID as a CloudFormation attribute, so look it up via a custom resource
    # that calls ec2:DescribeTransitGatewayAttachments, filtered to the VPN
    # connection's resource ID.
    for site, vpn_connection in ialirt_vpn.vpn_connections.items():
        describe_vpn_attachment = cr.AwsSdkCall(
            service="EC2",
            action="describeTransitGatewayAttachments",
            parameters={
                "Filters": [
                    {
                        "Name": "resource-id",
                        "Values": [vpn_connection.attr_vpn_connection_id],
                    },
                    {"Name": "resource-type", "Values": ["vpn"]},
                ]
            },
            physical_resource_id=cr.PhysicalResourceId.of(
                f"IalirtVpnTgwAttachmentLookup{site}"
            ),
        )
        vpn_attachment_lookup = cr.AwsCustomResource(
            ialirt_stack,
            f"IalirtVpnTgwAttachmentLookup{site}",
            on_create=describe_vpn_attachment,
            on_update=describe_vpn_attachment,
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE
            ),
        )
        vpn_attachment_id = vpn_attachment_lookup.get_response_field(
            "TransitGatewayAttachments.0.TransitGatewayAttachmentId"
        )

        # Add the transit gateway attachments to the route table.

        # Use this table to decide where to forward the traffic it receives.
        ec2.CfnTransitGatewayRouteTableAssociation(
            ialirt_stack,
            f"IalirtTgwRouteTableAssociationVpn{site}",
            transit_gateway_attachment_id=vpn_attachment_id,
            transit_gateway_route_table_id=tgw_route_table_id,
        )
        # Installs NOAA's routes into the table so the VPC attachment can
        # find its way back to NOAA on the return path.
        ec2.CfnTransitGatewayRouteTablePropagation(
            ialirt_stack,
            f"IalirtTgwRouteTablePropagationVpn{site}",
            transit_gateway_attachment_id=vpn_attachment_id,
            transit_gateway_route_table_id=tgw_route_table_id,
        )

    # AWS already publishes each tunnel's up/down state to CloudWatch
    # (namespace AWS/VPN, metric TunnelState) with no configuration needed
    # on our end. This dashboard just gives a single place to look at it,
    # via a search expression so it doesn't need to know the tunnels'
    # outside IPs (which aren't available as CloudFormation attributes).
    cloudwatch.Dashboard(
        ialirt_stack,
        "IalirtNoaaVpnDashboard",
        dashboard_name="IalirtNoaaVpnTunnelState",
        widgets=[
            [
                cloudwatch.GraphWidget(
                    title="NOAA VPN Tunnel State (1 = up, 0 = down)",
                    left=[
                        cloudwatch.MathExpression(
                            # Confirmed via `aws cloudwatch list-metrics
                            # --namespace AWS/VPN --metric-name TunnelState`:
                            # AWS only publishes this metric as single-
                            # dimension rollups (TunnelIpAddress alone, VpnId
                            # alone, or no dimension) -- there is no combined
                            # {TunnelIpAddress, VpnId} metric to search on.
                            expression=(
                                "SEARCH('{AWS/VPN,TunnelIpAddress} "
                                "MetricName=\"TunnelState\"', 'Average', 300)"
                            ),
                            label="",
                        )
                    ],
                    width=24,
                )
            ]
        ],
    )

    # Static route: send traffic for I-ALiRT EC2's Elastic IP into the VPC
    # attachment, where the NAT Gateway forwards it to the public internet.
    ec2.CfnTransitGatewayRoute(
        ialirt_stack,
        "IalirtTgwDefaultRouteToVpc",
        destination_cidr_block=f"{ialirt_eip_ip}/32",
        transit_gateway_route_table_id=tgw_route_table_id,
        transit_gateway_attachment_id=ialirt_vpc_attachment.attr_id,
    )

    # If it's a private address (10.x, 172.16-31.x, 192.168.x) or the VPN
    # tunnel's own link-local inside address (169.254.x), go back through
    # the TGW instead of the public internet. The link-local range is
    # needed because NAT Gateway restores the tunnel's own link-local
    # inside IP as the destination when un-NATting return traffic,
    # regardless of what prefix is advertised to AWS via BGP.
    def _add_return_routes(subnet_group: str, subnets: list) -> None:
        for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"):
            cidr_suffix = cidr.split("/")[0].replace(".", "")
            for i, subnet in enumerate(subnets):
                route = ec2.CfnRoute(
                    ialirt_stack,
                    f"Ialirt{subnet_group}Subnet{i}ReturnRoute{cidr_suffix}",
                    route_table_id=subnet.route_table.route_table_id,
                    destination_cidr_block=cidr,
                    transit_gateway_id=ialirt_transit_gateway.attr_id,
                )
                route.add_dependency(ialirt_vpc_attachment)

    _add_return_routes("Private", networking.vpc.private_subnets)
    _add_return_routes("Public", networking.vpc.public_subnets)
