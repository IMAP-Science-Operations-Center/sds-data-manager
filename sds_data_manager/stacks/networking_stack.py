"""Networking Stack"""
# Installed
from constructs import Construct
from aws_cdk import (
    Stack,
    aws_ec2 as ec2
)

#TODO: This is temporary and its settings may change
class NetworkingStack(Stack):
    """General purpose networking components"""

    def __init__(
            self,
            scope: Construct,
            construct_id: str,
            sds_id: str,
            **kwargs) -> None:

        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(self, "VPC", nat_gateways=1,
                           subnet_configuration=[
                               ec2.SubnetConfiguration(
                                   name="Public",
                                   subnet_type=ec2.SubnetType.PUBLIC
                               ),
                               ec2.SubnetConfiguration(
                                   subnet_type=ec2.SubnetType.PRIVATE_WITH_NAT,
                                   name="Private",
                                   cidr_mask=24
                               ),
                               ec2.SubnetConfiguration(
                                   subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                                   name="Isolated",
                                   cidr_mask=24)
                           ])

        # Setup a security group for the Fargate-generated EC2 instances.
        self.batch_security_group = ec2.SecurityGroup(self,
                                                      f"FargateInstanceSecurityGroup-{sds_id}",
                                                      vpc=self.vpc)