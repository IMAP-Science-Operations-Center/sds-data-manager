"""NetworkingStack Stack"""
# Installed
from aws_cdk import Environment, Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


#TODO: May not need everything here, but left it for now
class NetworkingStack(Stack):
    """General purpose networking components"""

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 sds_id: str,
                 env: Environment,
                 **kwargs) -> None:
        """NetworkingStack constructor

        Parameters
        ----------
        scope : App
        construct_id : str
        sds_id : str
            Name suffix for stack
        env : Environment
            Account and region
        """
        super().__init__(scope, construct_id, env=env, **kwargs)
        self.vpc = ec2.Vpc(self, f"VPC-{sds_id}",
                           gateway_endpoints={
                               "s3": ec2.GatewayVpcEndpointOptions(
                                   service=ec2.GatewayVpcEndpointAwsService.S3
                               )
                           },
                           nat_gateways=1,
                           subnet_configuration=[
                               ec2.SubnetConfiguration(
                                   name=f"Public-{sds_id}",
                                   subnet_type=ec2.SubnetType.PUBLIC
                               ),
                               ec2.SubnetConfiguration(
                                   subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                                   name=f"Private-{sds_id}",
                                   cidr_mask=24
                               ),
                               ec2.SubnetConfiguration(
                                   subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                                   name=f"Isolated-{sds_id}",
                                   cidr_mask=24)
                           ])
