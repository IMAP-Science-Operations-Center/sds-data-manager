"""Processing Stack
This is the module containing the general stack to be built for
computation of I-ALiRT algorithms
"""
from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from constructs import Construct


class IalirtProcessing(Stack):
    """A processing system for ialirt."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        repo: ecr.Repository,
        instance_type: str = "t3.micro",
        **kwargs,
    ) -> None:
        """Constructor

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        repo : ecr.Repository
            ECR repository containing the Docker image.
        """
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = vpc
        self.repo = repo
        self.instance_type = instance_type
        self.add_compute_resources()
        self.add_dynamodb_table()

    # Setup the EC2 resources
    def add_compute_resources(self):
        """EC2 compute environment."""

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
            f"docker pull {self.repo.repository_uri}:latest",
            f"docker run --rm -d -p 8080:8080 {self.repo.repository_uri}:latest",
        )

        # Security Group for the EC2 Instance
        security_group = ec2.SecurityGroup(
            self,
            "IalirtEC2SecurityGroup",
            vpc=self.vpc,
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
            instance_type=ec2.InstanceType(self.instance_type),
            machine_image=ec2.MachineImage.latest_amazon_linux2(),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=security_group,
            role=ec2_role,
            user_data=user_data,
        )

    # I-ALiRT IOIS DynamoDB
    # ingest-ugps: ingestion ugps - 64 bit
    # sct-vtcw: spacecraft time ugps - 64 bit
    # src-seq-ctr: increments with each packet (included in filename?)
    # ccsds-filename: filename of the packet
    def add_dynamodb_table(self):
        """DynamoDB Table."""
        dynamodb.Table(
            self,
            "DynamoDB-ialirt",
            table_name="ialirt-packets",
            partition_key=dynamodb.Attribute(
                name="ingest-time", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="spacecraft-time", type=dynamodb.AttributeType.STRING
            ),
            # on-demand
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=True,
        )
