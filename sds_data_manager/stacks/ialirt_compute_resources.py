"""
This module provides the IalirtEC2Resources class which sets up I-Alirt
compute resources utilizing EC2 as the compute environment (for now).
"""
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from constructs import Construct


class IalirtEC2Resources(Construct):
    """EC2 compute environment."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        repo: ecr.Repository,
        instance_type: str = "t3.micro",
    ):
        """Constructor

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        vpc : ec2.Vpc
            VPC in which to create compute instances.
        repo : ecr.Repository
            Container repo.
        instance_type : str, Optional
            Type of EC2 instance to launch.
        """
        super().__init__(scope, construct_id)

        # Define user data script
        # - Updates the instance
        # - Installs Docker
        # - Starts the Docker
        # - Logs into AWS ECR to pull the image onto the instance
        # - Pulls the Docker image
        # - Runs the Docker container
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "yum update -y",
            "amazon-linux-extras install docker -y",
            "systemctl start docker",
            "systemctl enable docker",
            "$(aws ecr get-login --no-include-email --region us-west-2 | bash)",
            f"docker pull {repo.repository_uri}:latest",
            f"docker run --rm -d -p 8080:8080 {repo.repository_uri}:latest",
        )

        # Security Group for the EC2 Instance
        security_group = ec2.SecurityGroup(
            self,
            "IalirtEC2SecurityGroup",
            vpc=vpc,
            description="Security group for Ialirt EC2 instance",
        )

        # Allow ingress to LASP IP address range and specific port
        security_group.add_ingress_rule(
            ec2.Peer.ipv4("128.138.131.0/24"),
            ec2.Port.tcp(8080),
            "Allow inbound traffic on TCP port 8080",
        )

        # Create an IAM role for the EC2 instance
        # - Read-only access to AWS ECR
        # - Basic instance management via AWS Systems Manager
        # Note: the Systems Manager provides a secure way for
        # users to interact and access the EC2 during development.
        ec2_role = iam.Role(
            self,
            "IalirtEC2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonEC2ContainerRegistryReadOnly"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                ),
            ],
        )

        # Create an EC2 instance
        ec2.Instance(
            self,
            "IalirtEC2Instance",
            instance_type=ec2.InstanceType(instance_type),
            machine_image=ec2.MachineImage.latest_amazon_linux2(),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=security_group,
            role=ec2_role,
            user_data=user_data,
        )
