"""Configure the i-alirt processing stack.

This is the module containing the general stack to be built for computation of
I-ALiRT algorithms. It was built using best practices as shown here:

https://docs.aws.amazon.com/AmazonECS/latest/bestpracticesguide/networking-inbound.html
https://aws.amazon.com/elasticloadbalancing/features/#Product_comparisons
"""

from aws_cdk import CfnOutput
from aws_cdk import aws_autoscaling as autoscaling
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct


class IalirtProcessing(Construct):
    """A processing system for I-ALiRT."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        repo: ecr.Repository,
        processing_name: str,
        ialirt_ports: list[int],
        container_port: int,
        ialirt_bucket: s3.Bucket,
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
        processing_name : str
            Name of the processing stack.
        ialirt_ports : list[int]
            List of ports to listen on for incoming traffic.
        container_port : int
            Port to be used by the container.
        ialirt_bucket: s3.Bucket
            S3 bucket
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        self.ports = ialirt_ports
        self.container_port = container_port
        self.vpc = vpc
        self.repo = repo
        self.s3_bucket_name = ialirt_bucket.bucket_name

        # Add a security group in which network load balancer will reside
        self.create_load_balancer_security_group(processing_name)

        # Create security group in which containers will reside
        self.create_ecs_security_group(processing_name)

        # Add an ecs service and cluster for each container
        self.add_compute_resources(processing_name)
        # Add load balancer for each container
        self.add_load_balancer(processing_name)
        # Add autoscaling for each container
        self.add_autoscaling(processing_name)

    def create_ecs_security_group(self, processing_name):
        """Create and return a security group for containers."""
        self.ecs_security_group = ec2.SecurityGroup(
            self,
            f"IalirtEcsSecurityGroup{processing_name}",
            vpc=self.vpc,
            description="Security group for Ialirt",
            allow_all_outbound=True,
        )

        # Only allow traffic from the NLB security group
        self.ecs_security_group.add_ingress_rule(
            peer=ec2.Peer.security_group_id(
                self.load_balancer_security_group.security_group_id
            ),
            connection=ec2.Port.tcp(self.container_port),
            description=f"Allow inbound traffic from the NLB on "
            f"TCP port {self.container_port}",
        )

    def create_load_balancer_security_group(self, processing_name):
        """Create and return a security group for load balancers."""
        # Create a security group for the NLB
        self.load_balancer_security_group = ec2.SecurityGroup(
            self,
            f"NLBSecurityGroup{processing_name}",
            vpc=self.vpc,
            description="Security group for the Ialirt NLB",
        )

        # Allow inbound and outbound traffic from a specific port and
        # LASP IP address range.
        for port in self.ports:
            self.load_balancer_security_group.add_ingress_rule(
                # TODO: allow IP addresses from partners
                peer=ec2.Peer.ipv4("128.138.131.0/24"),
                connection=ec2.Port.tcp(port),
                description=f"Allow inbound traffic on TCP port {port}",
            )

            # Allow outbound traffic.
            self.load_balancer_security_group.add_egress_rule(
                peer=ec2.Peer.ipv4("128.138.131.0/24"),
                connection=ec2.Port.tcp(port),
                description=f"Allow outbound traffic on TCP port {port}",
            )

    def add_compute_resources(self, processing_name):
        """Add ECS compute resources for a container."""
        # ECS Cluster manages EC2 instances on which containers are deployed.
        self.ecs_cluster = ecs.Cluster(
            self, f"IalirtCluster{processing_name}", vpc=self.vpc
        )

        # Add IAM role and policy for S3 access
        task_role = iam.Role(
            self,
            f"IalirtTaskRole{processing_name}",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:ListBucket", "s3:PutObject"],
                resources=[
                    f"arn:aws:s3:::{self.s3_bucket_name}",
                    f"arn:aws:s3:::{self.s3_bucket_name}/*",
                ],
            )
        )

        # Specifies the networking mode as AWS_VPC.
        # ECS tasks in AWS_VPC mode can be registered with
        # Network Load Balancers (NLB).
        task_definition = ecs.Ec2TaskDefinition(
            self,
            f"IalirtTaskDef{processing_name}",
            network_mode=ecs.NetworkMode.AWS_VPC,
            task_role=task_role,
        )

        # Adds a container to the ECS task definition
        # Logging is configured to use AWS CloudWatch Logs.
        container = task_definition.add_container(
            f"IalirtContainer{processing_name}",
            image=ecs.ContainerImage.from_ecr_repository(
                self.repo, f"latest-{processing_name.lower()}"
            ),
            # Allowable values:
            # https://docs.aws.amazon.com/cdk/api/v2/docs/
            # aws-cdk-lib.aws_ecs.TaskDefinition.html#cpu
            memory_limit_mib=512,
            cpu=256,
            logging=ecs.LogDrivers.aws_logs(stream_prefix=f"Ialirt{processing_name}"),
            environment={"S3_BUCKET": self.s3_bucket_name},
            # Ensure the ECS task is running in privileged mode,
            # which allows the container to use FUSE.
            privileged=True,
        )

        # Map ports to container
        # NLB needs to know which port on the EC2 instances
        # it should forward the traffic to
        port_mapping = ecs.PortMapping(
            container_port=self.container_port,
            host_port=self.container_port,
            protocol=ecs.Protocol.TCP,
        )
        container.add_port_mappings(port_mapping)

        # ECS Service is a configuration that
        # ensures application can run and maintain
        # instances of a task definition.
        self.ecs_service = ecs.Ec2Service(
            self,
            f"IalirtService{processing_name}",
            cluster=self.ecs_cluster,
            task_definition=task_definition,
            security_groups=[self.ecs_security_group],
            desired_count=1,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

    def add_autoscaling(self, processing_name):
        """Add autoscaling resources."""
        # This auto-scaling group is used to manage the
        # number of instances in the ECS cluster. If an instance
        # becomes unhealthy, the auto-scaling group will replace it.
        auto_scaling_group = autoscaling.AutoScalingGroup(
            self,
            f"AutoScalingGroup{processing_name}",
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
            f"AsgCapacityProvider{processing_name}",
            auto_scaling_group=auto_scaling_group,
        )

        self.ecs_cluster.add_asg_capacity_provider(capacity_provider)

        # Allow inbound traffic from the Network Load Balancer
        # to the security groups associated with the EC2 instances
        # within the Auto Scaling Group.
        for port in self.ports:
            auto_scaling_group.connections.allow_from(
                self.load_balancer, ec2.Port.tcp(port)
            )

    def add_load_balancer(self, processing_name):
        """Add a load balancer for a container."""
        # Create the Network Load Balancer and
        # place it in a public subnet.
        self.load_balancer = elbv2.NetworkLoadBalancer(
            self,
            f"IalirtNLB{processing_name}",
            vpc=self.vpc,
            security_groups=[self.load_balancer_security_group],
            internet_facing=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        # Create a listener for each port specified
        for port in self.ports:
            listener = self.load_balancer.add_listener(
                f"Listener{processing_name}{port}",
                port=port,
                protocol=elbv2.Protocol.TCP,
            )

            # Register the ECS service as a target for the listener
            listener.add_targets(
                f"Target{processing_name}{self.container_port}",
                port=self.container_port,
                targets=[self.ecs_service],
            )

            # This simply prints the DNS name of the
            # load balancer in the terminal.
            CfnOutput(
                self,
                f"LoadBalancerDNS{processing_name}{port}",
                value=f"http://{self.load_balancer.load_balancer_dns_name}:{port}",
            )
