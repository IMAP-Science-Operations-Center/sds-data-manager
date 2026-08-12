"""Configure the I-ALiRT VPN connections to NOAA N-Wave."""

from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class IalirtVpnConstruct(Construct):
    """NOAA N-Wave customer gateways and VPN connections for I-ALiRT."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        transit_gateway_id: str,
        psk: str,
        wash_ip: str,
        denv_ip: str,
        **kwargs,
    ) -> None:
        """Create NOAA N-Wave customer gateways and VPN connections.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        transit_gateway_id : str
            The Transit Gateway to attach the VPN connections to. A Transit
            Gateway is used (rather than a Virtual Private Gateway) because a
            VGW cannot route decrypted VPN traffic to a NAT Gateway, and a NAT
            Gateway is required so NOAA's traffic reaches I-ALiRT via its
            stable Elastic IP instead of an EC2 private IP that changes
            whenever the Auto Scaling Group replaces the instance.
        psk : str
            Pre-shared key for IKE authentication. Pass a CDK token from
            ``secret_value_from_json(...).unsafe_unwrap()`` so the value is
            resolved by CloudFormation at deploy time and never appears in
            the template.
        wash_ip : str
            NOAA border router public IP at McLean, VA (WASH), retrieved from SSM.
        denv_ip : str
            NOAA border router public IP at Denver, CO (DENV), retrieved from SSM.
        kwargs : dict
            Keyword arguments.
        """
        super().__init__(scope, construct_id, **kwargs)

        # Define the crypto settings for the IPSec tunnel, as specified
        # in the N-Wave ICD (NOAA0550).
        #
        # Phase 1 (IKE) — the handshake phase where both sides authenticate each other
        # and agree on encryption keys. Uses pre-shared key (PSK)
        # resolved at deploy time.
        #   - IKEv2 only (NOAA requirement)
        #   - AES-256 encryption
        #   - SHA2-256 integrity
        #   - DH group 14 for key exchange
        #   - 28800s (8 hour) lifetime
        #
        # Phase 2 (ESP) — the data phase where actual traffic is encrypted.
        #   - AES-128 or AES-256 encryption
        #   - HMAC-SHA2-256-128 integrity
        #   - DH group 14 (PFS — Perfect Forward Secrecy)
        #   - 3600s (1 hour) lifetime
        tunnel = ec2.CfnVPNConnection.VpnTunnelOptionsSpecificationProperty(
            pre_shared_key=psk,
            ike_versions=[
                ec2.CfnVPNConnection.IKEVersionsRequestListValueProperty(value="ikev2")
            ],
            phase1_encryption_algorithms=[
                ec2.CfnVPNConnection.Phase1EncryptionAlgorithmsRequestListValueProperty(
                    value="AES256"
                )
            ],
            phase1_integrity_algorithms=[
                ec2.CfnVPNConnection.Phase1IntegrityAlgorithmsRequestListValueProperty(
                    value="SHA2-256"
                )
            ],
            phase1_dh_group_numbers=[
                ec2.CfnVPNConnection.Phase1DHGroupNumbersRequestListValueProperty(
                    value=14
                )
            ],
            phase1_lifetime_seconds=28800,
            phase2_encryption_algorithms=[
                ec2.CfnVPNConnection.Phase2EncryptionAlgorithmsRequestListValueProperty(
                    value="AES128"
                ),
                ec2.CfnVPNConnection.Phase2EncryptionAlgorithmsRequestListValueProperty(
                    value="AES256"
                ),
            ],
            phase2_integrity_algorithms=[
                ec2.CfnVPNConnection.Phase2IntegrityAlgorithmsRequestListValueProperty(
                    value="SHA2-256"
                )
            ],
            phase2_dh_group_numbers=[
                ec2.CfnVPNConnection.Phase2DHGroupNumbersRequestListValueProperty(
                    value=14
                )
            ],
            phase2_lifetime_seconds=3600,
        )

        # Customer Gateway - AWS's record of NOAA's router so that AWS can recognize
        # and accept the incoming encrypted packets.

        # Every AWS Site-to-Site VPN connection automatically provisions
        # two auto-assigned tunnel IPs.
        # These LASP IKE Gateways must be given to NOAA.
        self.vpn_connections: dict[str, ec2.CfnVPNConnection] = {}
        # NOAA's ASN per the ICD, per site.
        noaa_asns = {"WASH": 64892, "DENV": 64893}
        for site, ip in {"WASH": wash_ip, "DENV": denv_ip}.items():
            # AWS needs to know the router's public IP and ASN to establish the tunnel.
            cgw = ec2.CfnCustomerGateway(
                self,
                f"NoaaCustomerGateway{site}",
                bgp_asn=noaa_asns[site],
                ip_address=ip,
                type="ipsec.1",
            )

            # Create the VPN connection between our Transit Gateway (TGW) and
            # NOAA's customer gateway. Each connection gets two tunnels by
            # default (AWS requirement for redundancy) — both use the same
            # crypto settings. BGP is used (static_routes_only=False) so that
            # if one site (WASH or DENV) goes down, BGP automatically reroutes
            # traffic through the other. Data flows one way: NOAA sends to us.
            # We do not send to NOAA.
            self.vpn_connections[site] = ec2.CfnVPNConnection(
                self,
                f"NoaaVpnConnection{site}",
                customer_gateway_id=cgw.ref,
                transit_gateway_id=transit_gateway_id,
                type="ipsec.1",
                static_routes_only=False,
                vpn_tunnel_options_specifications=[tunnel, tunnel],
            )
