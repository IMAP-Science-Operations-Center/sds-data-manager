"""Configure the i-alirt processing stack."""

import pathlib

import aws_cdk as cdk
from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_autoscaling as autoscaling
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_python_alpha as lambda_alpha_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class IalirtProcessing(Construct):
    """A processing system for I-ALiRT."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env: cdk.Environment,
        vpc: ec2.Vpc,
        ports: list[int],
        ialirt_bucket: s3.Bucket,
        secret_name: str,
        **kwargs,
    ) -> None:
        """Construct the i-alirt processing stack.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        env : cdk.Environment
            The environment in which to deploy the stack.
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        ports : list[int]
            List of ports to listen on for incoming traffic and used by container.
        ialirt_bucket: s3.Bucket
            S3 bucket
        secret_name : str,
            Database secret_name for Secrets Manager
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        self.ports = ports
        self.vpc = vpc
        self.s3_bucket_name = ialirt_bucket.bucket_name
        self.secret_name = secret_name
        self.region = env.region

        # Create security group in which containers will reside
        self.create_ecs_security_group()

        # Add an ecs service and cluster for each container
        self.add_compute_resources()
        # Add autoscaling for each container
        self.add_autoscaling()

    def create_ecs_security_group(self):
        """Create and return a security group for containers."""
        self.ecs_security_group = ec2.SecurityGroup(
            self,
            "IalirtEcsSecurityGroup",
            vpc=self.vpc,
            description="Security group for Ialirt",
            allow_all_outbound=True,
        )

        # Allow inbound and outbound traffic from a specific port and IP.
        # IPs: LASP IP, BlueNet (tlm relay)
        ip_ranges = ["128.138.131.0/24", "198.118.1.14/32"]
        for port in self.ports:
            for ip_range in ip_ranges:
                self.ecs_security_group.add_ingress_rule(
                    # TODO: allow IP addresses from partners
                    peer=ec2.Peer.ipv4(ip_range),
                    connection=ec2.Port.tcp(port),
                    description=f"Allow inbound traffic on TCP port {port}",
                )

                # Allow outbound traffic.
                self.ecs_security_group.add_egress_rule(
                    peer=ec2.Peer.ipv4(ip_range),
                    connection=ec2.Port.tcp(port),
                    description=f"Allow outbound traffic on TCP port {port}",
                )

    def add_compute_resources(self):
        """Add ECS compute resources for a container."""
        # ECS Cluster manages EC2 instances on which containers are deployed.
        self.ecs_cluster = ecs.Cluster(self, "IalirtCluster", vpc=self.vpc)

        # Retrieve the secrets from Secrets Manager.
        nexus_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "NexusCredentials", secret_name=self.secret_name
        )

        # Add IAM role and policy for S3 access
        task_role = iam.Role(
            self,
            "IalirtTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:GetObject",
                    "s3:ListBucket",
                    "s3:PutObject",
                    "secretsmanager:GetSecretValue",
                ],
                resources=[
                    f"arn:aws:s3:::{self.s3_bucket_name}",
                    f"arn:aws:s3:::{self.s3_bucket_name}/*",
                    nexus_secret.secret_arn,
                ],
            )
        )

        # Required for pulling images from Nexus.
        # https://docs.aws.amazon.com/AmazonECS/latest/developerguide/private-auth.html
        execution_role = iam.Role(
            self,
            "IalirtTaskExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy",
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "SecretsManagerReadWrite"
                ),
            ],
        )

        # Grant Secrets Manager access for Nexus credentials.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[nexus_secret.secret_arn],
            )
        )

        # Specifies the networking mode as HOST.
        # In "HOST" you can access the container using the EC2 public IP
        # that is automatically assigned.
        # The ECS tasks automatically inherit the EC2 instance
        # Elastic IP so that they always use a publicly accessible IP address.
        task_definition = ecs.Ec2TaskDefinition(
            self,
            "IalirtTaskDef",
            network_mode=ecs.NetworkMode.HOST,
            task_role=task_role,
            execution_role=execution_role,
        )

        # Adds a container to the ECS task definition
        # Logging is configured to use AWS CloudWatch Logs.
        task_definition.add_container(
            "IalirtContainer",
            image=ecs.ContainerImage.from_registry(
                "lasp-registry.colorado.edu/ialirt/ialirt:latest",
                credentials=nexus_secret,
            ),
            # Allowable values:
            # https://docs.aws.amazon.com/cdk/api/v2/docs/
            # aws-cdk-lib.aws_ecs.TaskDefinition.html#cpu
            memory_limit_mib=512,
            cpu=256,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="Ialirt"),
            environment={"S3_BUCKET": self.s3_bucket_name},
            # Ensure the ECS task is running in privileged mode,
            # which allows the container to use FUSE.
            privileged=True,
        )

        # ECS Service is a configuration that
        # ensures application can run and maintain
        # instances of a task definition.
        self.ecs_service = ecs.Ec2Service(
            self,
            "IalirtService",
            cluster=self.ecs_cluster,
            task_definition=task_definition,
            desired_count=1,
        )

    def create_autoscaling_event_rule(
        self,
        assign_eip_lambda: lambda_alpha_.PythonFunction,
        auto_scaling_group: autoscaling.AutoScalingGroup,
    ) -> None:
        """Create Rules to trigger Lambda on Auto Scaling Group instance launch."""
        deploy_rule = events.Rule(
            self,
            "AssignEipOnEc2InstanceLaunch",
            rule_name="assign-eip-ec2-instance-launch",
            event_pattern=events.EventPattern(
                source=["aws.ec2"],
                detail_type=["EC2 Instance State-change Notification"],
                detail={
                    "state": ["running"],
                },
            ),
        )
        asg_lifecycle_rule = events.Rule(
            self,
            "AssignEipOnInstanceLaunch",
            rule_name="assign-eip-instance-launch",
            event_pattern=events.EventPattern(
                source=["aws.autoscaling"],
                detail_type=["EC2 Instance-launch Lifecycle Action"],
                detail={
                    "AutoScalingGroupName": [auto_scaling_group.auto_scaling_group_name]
                },
            ),
        )

        deploy_rule.add_target(targets.LambdaFunction(assign_eip_lambda))
        asg_lifecycle_rule.add_target(targets.LambdaFunction(assign_eip_lambda))

    def create_lambda_function(
        self,
    ) -> lambda_alpha_.PythonFunction:
        """Create and return the Lambda function."""
        lambda_role = iam.Role(
            self,
            "IalirtEipLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2FullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AutoScalingFullAccess"),
            ],
        )

        eip_lambda = lambda_alpha_.PythonFunction(
            self,
            id="IalirtAssignEipLambda",
            function_name="ialirt-eip",
            entry=str(
                pathlib.Path(__file__).parent.parent.joinpath("lambda_code").resolve()
            ),
            index="IAlirtCode/ialirt_eip.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=Duration.minutes(1),
            memory_size=1000,
            role=lambda_role,
        )

        # The resource is deleted when the stack is deleted.
        eip_lambda.apply_removal_policy(RemovalPolicy.DESTROY)

        return eip_lambda

    def add_autoscaling(self):
        """Add autoscaling resources."""
        # This auto-scaling group is used to manage the
        # number of instances in the ECS cluster. If an instance
        # becomes unhealthy, the auto-scaling group will replace it.
        auto_scaling_group = autoscaling.AutoScalingGroup(
            self,
            "AutoScalingGroup",
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.LARGE
            ),
            machine_image=ecs.EcsOptimizedImage.amazon_linux2(),
            vpc=self.vpc,
            desired_capacity=1,
            min_capacity=1,
            max_capacity=2,  # Allow one extra instance during updates
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC,
            ),
            associate_public_ip_address=True,
            security_group=self.ecs_security_group,
        )

        auto_scaling_group.apply_removal_policy(RemovalPolicy.DESTROY)
        eip_lambda = self.create_lambda_function()
        self.create_autoscaling_event_rule(eip_lambda, auto_scaling_group)

        # Attach the AmazonSSMManagedInstanceCore policy for SSM access
        auto_scaling_group.role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonSSMManagedInstanceCore"
            )
        )
        # Add EventBridgeFullAccess policy for EventBridge access
        auto_scaling_group.role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonEventBridgeFullAccess"
            )
        )

        autoscaling.LifecycleHook(
            self,
            "EipAssignmentHook",
            auto_scaling_group=auto_scaling_group,
            lifecycle_transition=autoscaling.LifecycleTransition.INSTANCE_LAUNCHING,
            default_result=autoscaling.DefaultResult.CONTINUE,
            heartbeat_timeout=Duration.minutes(
                5
            ),  # Allow up to 5 minutes for EIP assignment
            lifecycle_hook_name="EipAssignmentHook",
            notification_metadata="EIP Assignment Lifecycle Hook",
        )

        # integrates ECS with EC2 Auto Scaling Groups
        # to manage the scaling and provisioning of the underlying
        # EC2 instances based on the requirements of ECS tasks
        capacity_provider = ecs.AsgCapacityProvider(
            self,
            "AsgCapacityProvider",
            auto_scaling_group=auto_scaling_group,
            enable_managed_termination_protection=False,
            enable_managed_scaling=False,
        )

        self.ecs_cluster.add_asg_capacity_provider(capacity_provider)
