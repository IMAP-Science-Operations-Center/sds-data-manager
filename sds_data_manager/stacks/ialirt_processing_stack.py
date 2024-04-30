"""Configure the i-alirt processing stack.

This is the module containing the general stack to be built for computation of
I-ALiRT algorithms.
"""

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_autoscaling as autoscaling
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from constructs import Construct

# Ports configuration for various containers within the IALiRT system
IALIRT_PORTS = [8080, 8081]


class IalirtProcessing(Stack):
    """A processing system for I-ALiRT."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        repo: ecr.Repository,
        **kwargs,
    ) -> None:
        """Construct the i-alirt processing stack.

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
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = vpc
        self.repo = repo
        self.add_dynamodb_table()

        # Create single security group in which
        # both containers will reside
        self.create_ecs_security_group()

        # Add a single security group in which
        # both application load balancers will reside
        self.create_load_balancer_security_group()

        # Add an ecs service and cluster for each container
        self.add_compute_resources("Container1", IALIRT_PORTS, self.ecs_security_group)
        # Add load balancer for each container
        load_balancer = self.add_load_balancer(
            "Container1", IALIRT_PORTS, self.load_balancer_security_group
        )
        # Add autoscaling for each container
        self.add_autoscaling("Container1", load_balancer, IALIRT_PORTS)

    def create_ecs_security_group(self):
        """Create and return a security group for containers."""
        self.ecs_security_group = ec2.SecurityGroup(
            self,
            "IalirtEcsSecurityGroup",
            vpc=self.vpc,
            description="Security group for Ialirt",
            allow_all_outbound=True,
        )

        for port in IALIRT_PORTS:
            # Add ingress rule for each port
            self.ecs_security_group.add_ingress_rule(
                peer=ec2.Peer.any_ipv4(),
                connection=ec2.Port.tcp(port),
                description="Allow inbound traffic on TCP port",
            )

    def create_load_balancer_security_group(self):
        """Create and return a security group for load balancers."""
        # Create a security group for the ALB
        self.load_balancer_security_group = ec2.SecurityGroup(
            self,
            "ALBSecurityGroup",
            vpc=self.vpc,
            description="Security group for the Ialirt ALB",
        )

        # Allow inbound and outbound traffic from a specific port and
        # any ipv4 address.
        for port in IALIRT_PORTS:
            self.load_balancer_security_group.add_ingress_rule(
                peer=ec2.Peer.any_ipv4(),
                connection=ec2.Port.tcp(port),
                description=f"Allow inbound traffic on TCP port {port}",
            )

            # Allow all outbound traffic.
            self.load_balancer_security_group.add_egress_rule(
                peer=ec2.Peer.any_ipv4(),
                connection=ec2.Port.tcp(port),
                description=f"Allow outbound traffic on TCP port {port}",
            )

    def add_compute_resources(self, container_name, ecs_security_group):
        """Add ECS compute resources for a container."""
        # ECS Cluster manages EC2 instances on which containers are deployed.
        self.ecs_cluster = ecs.Cluster(
            self, f"IalirtCluster{container_name}", vpc=self.vpc
        )

        # This task definition specifies the networking mode as AWS_VPC.
        # ECS tasks in AWS_VPC mode can be registered with
        # Application Load Balancers (ALB).
        task_definition = ecs.Ec2TaskDefinition(
            self, f"IalirtTaskDef{container_name}", network_mode=ecs.NetworkMode.AWS_VPC
        )

        # Adds a container to the ECS task definition
        # Logging is configured to use AWS CloudWatch Logs.
        container = task_definition.add_container(
            f"IalirtContainer{container_name}",
            image=ecs.ContainerImage.from_ecr_repository(self.repo, "latest"),
            # Allowable values:
            # https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_ecs.TaskDefinition.html#cpu
            memory_limit_mib=512,
            cpu=256,
            logging=ecs.LogDrivers.aws_logs(stream_prefix=f"Ialirt{container_name}"),
        )

        for port in IALIRT_PORTS:
            # Map ports to container
            port_mapping = ecs.PortMapping(
                container_port=port,
                host_port=port,
                protocol=ecs.Protocol.TCP,
            )
            container.add_port_mappings(port_mapping)

        # ECS Service is a configuration that
        # ensures application can run and maintain
        # instances of a task definition.
        self.ecs_service = ecs.Ec2Service(
            self,
            f"IalirtService{container_name}",
            cluster=self.ecs_cluster,
            task_definition=task_definition,
            security_groups=[ecs_security_group],
            desired_count=1,
        )

    def add_autoscaling(self, container_name, load_balancer):
        """Add autoscaling resources."""
        # This auto scaling group is used to manage the
        # number of instances in the ECS cluster. If an instance
        # becomes unhealthy, the auto scaling group will replace it.
        auto_scaling_group = autoscaling.AutoScalingGroup(
            self,
            f"AutoScalingGroup{container_name}",
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.MICRO
            ),
            machine_image=ecs.EcsOptimizedImage.amazon_linux2(),
            vpc=self.vpc,
            desired_capacity=1,
        )

        # integrates ECS with EC2 Auto Scaling Groups
        # to manage the scaling and provisioning of the underlying
        # EC2 instances based on the requirements of ECS tasks
        capacity_provider = ecs.AsgCapacityProvider(
            self,
            f"AsgCapacityProvider{container_name}",
            auto_scaling_group=auto_scaling_group,
        )

        self.ecs_cluster.add_asg_capacity_provider(capacity_provider)

        # Allow inbound traffic from the Application Load Balancer
        # to the security groups associated with the EC2 instances
        # within the Auto Scaling Group.
        for port in IALIRT_PORTS:
            auto_scaling_group.connections.allow_from(load_balancer, ec2.Port.tcp(port))

    def add_load_balancer(self, container_name, load_balancer_security_group):
        """Add a load balancer for a container."""
        # Create the Application Load Balancer and
        # place it in a public subnet.
        load_balancer = elbv2.ApplicationLoadBalancer(
            self,
            f"IalirtALB{container_name}",
            vpc=self.vpc,
            security_group=load_balancer_security_group,
            internet_facing=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        # Create a listener for each port specified
        for port in IALIRT_PORTS:
            listener = load_balancer.add_listener(
                f"Listener{container_name}{port}",
                port=port,
                open=True,
                protocol=elbv2.ApplicationProtocol.HTTP,
            )

            # Register the ECS service as a target for the listener
            listener.add_targets(
                f"Target{container_name}{port}",
                port=port,
                targets=[self.ecs_service],
                protocol=elbv2.ApplicationProtocol.HTTP,
            )

            # This simply prints the DNS name of the
            # load balancer in the terminal.
            CfnOutput(
                self,
                f"LoadBalancerDNS{container_name}{port}",
                value=f"http://{load_balancer.load_balancer_dns_name}:{port}",
            )

        return load_balancer

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
