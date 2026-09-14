"""Construct resources needed for Dagster."""
#!/usr/bin/env python3

import aws_cdk as cdk
from aws_cdk import (
    RemovalPolicy,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_ecr as ecr,
)
from aws_cdk import (
    aws_ecs as ecs,
)
from aws_cdk import (
    aws_ecs_patterns as ecs_patterns,
)
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_rds as rds,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from aws_cdk.aws_ecr import Repository
from aws_cdk.aws_ecr_assets import DockerImageAsset, Platform
from cdk_ecr_deployment import DockerImageName, ECRDeployment
from constructs import Construct


class EcrConstruct(Construct):
    """Construct the ECR Resources."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        repo_name: str,
        **kwargs,
    ) -> None:
        """DataStorageConstruct constructor.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        repo_name : str
            The name to give the repository
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        repo_lifecycle_rule = ecr.LifecycleRule(
            description="Remove old untagged images",
            max_image_age=cdk.Duration.days(7),  # Remove after 7 days
            tag_status=ecr.TagStatus.UNTAGGED,
        )

        self.container_repo = ecr.Repository(
            self,
            construct_id,
            lifecycle_rules=[repo_lifecycle_rule],
            repository_name=repo_name,
            empty_on_delete=True,
            removal_policy=RemovalPolicy.DESTROY,
        )


class DagsterDockerImageConstruct(Construct):
    """Construct the actual Docker image."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        image_name: str,
        directory: str,
        ecr: str,
        file: str = "Dockerfile",
        docker_tag: str = "latest",
        **kwargs,
    ) -> None:
        """DagsterDockerImageConstruct constructor.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        image_name : str
            The name to give the image
        directory : str
            The directory of the Dockerfile
        ecr : str
            The URI of the ECR to push the Docker image to
        file : str
            The name of the Dockerfile to use
        docker_tag : str
            The tag to give the docker image once pushed
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        self.asset = DockerImageAsset(
            self,
            image_name + "_image",
            directory=directory,
            file=file,
            platform=Platform.LINUX_AMD64,
        )

        self.image = ECRDeployment(
            self,
            image_name + "_copy",
            src=DockerImageName(self.asset.image_uri),
            dest=DockerImageName(ecr + ":" + docker_tag),
            memory_limit=4096,
        )


class DagsterDatabaseConstruct(Construct):
    """Construct the database Dagster needs to keep track of processing state."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        sg,
        **kwargs,
    ) -> None:
        """DagsterDatabaseConstruct constructor.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        vpc : ec2.IVpc
            The VPC to use
        sg : ISecurityGroup
            The security group to use
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        self.db_secret = secretsmanager.Secret(
            self,
            "DagsterDatabaseSecret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username":"dagster"}',  # noqa: S106
                generate_string_key="password",
                exclude_characters='"@/\\',
            ),
        )

        self.db_instance = rds.DatabaseInstance(
            self,
            "DagsterStorageDB",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.XLARGE2
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            credentials=rds.Credentials.from_secret(self.db_secret),
            database_name="dagster",
            security_groups=[sg],
            removal_policy=RemovalPolicy.RETAIN,  # Retain data on stack updates
        )


class DagsterS3LoggingBucket(Construct):
    """Construct the database Dagster needs to store logs from asset runs."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs,
    ) -> None:
        """DagsterS3LoggingBucket constructor.

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

        self.logs_bucket = s3.Bucket(
            self,
            "DagsterComputeLogsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )


class DagsterEcsConstruct(Construct):
    """ECS Fargate construct for setting up all ECS tasks."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        sg,
        dagster_env_vars,
        db_secret,
        certificate,
        root_domain_name,
        domain,
        **kwargs,
    ) -> None:
        """Initialize the Construct."""
        super().__init__(scope, construct_id, **kwargs)

        # ECS Cluster
        cluster = ecs.Cluster(self, "DagsterCluster", vpc=vpc)

        # Execution role for pulling images and writing logs
        execution_role = iam.Role(
            self,
            "DagsterExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )

        # Task role
        task_role = iam.Role(
            self,
            "DagsterTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        # TODO: Terrible idea for now
        task_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess")
        )

        dagster_repo = Repository.from_repository_name(
            self, construct_id, repository_name="dagsterimage"
        )
        ecr_image = ecs.EcrImage(dagster_repo, "latest")

        # These tasks will run the Assets
        run_task_def = ecs.FargateTaskDefinition(
            self,
            "DagsterRunBaseTaskDef",
            cpu=4096,
            memory_limit_mib=8192,
            execution_role=execution_role,
            task_role=task_role,
        )
        run_task_def.add_container(
            "dagster-run",  # Must match standard naming expectation
            image=ecr_image,
            environment=dagster_env_vars,
            secrets={
                "DAGSTER_PG_PASSWORD": ecs.Secret.from_secrets_manager(
                    db_secret, "password"
                )
            },
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="DagsterRuns",
                log_group=logs.LogGroup(
                    self, "RunLogs", removal_policy=RemovalPolicy.DESTROY
                ),
            ),
        )
        dagster_env_vars["DAGSTER_RUN_BASE_TASK_DEF_ARN"] = (
            run_task_def.task_definition_arn
        )

        ### Dagster Webserver
        webserver_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "DagsterWebserver",
            cluster=cluster,
            cpu=4096,
            memory_limit_mib=8192,
            desired_count=1,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecr_image,
                command=[
                    "dagster-webserver",
                    "-h",
                    "0.0.0.0",
                    "-p",
                    "3000",
                    "-w",
                    "sds_data_manager/orchestration/workspace.yaml",
                ],
                container_port=3000,
                environment=dagster_env_vars,
                execution_role=execution_role,
                task_role=task_role,
                log_driver=ecs.LogDriver.aws_logs(
                    stream_prefix="DagsterWebserver",
                    log_group=logs.LogGroup(
                        self,
                        "DagsterWebserverLogs",
                        removal_policy=RemovalPolicy.DESTROY,
                    ),
                ),
                secrets={
                    "DAGSTER_PG_PASSWORD": ecs.Secret.from_secrets_manager(
                        db_secret, "password"
                    )
                },
            ),
            public_load_balancer=True,
            open_listener=False,
            health_check_grace_period=cdk.Duration.seconds(300),
            certificate=certificate,
            domain_name=f"dagster.{root_domain_name}",
            domain_zone=domain.hosted_zone,
            redirect_http=True,
        )

        webserver_service.service.connections.allow_to(
            sg, ec2.Port.tcp(5432), "Allow Dagster Webserver to access RDS"
        )

        allowed_cidrs = [
            "128.138.131.0/24",  # LASP
            "128.112.0.0/16",  # Princeton
            "140.180.0.0/16",  # Princeton
            "204.153.48.0/22",  # Princeton
            "12.161.8.0/24",  # Princeton
            "12.161.10.0/24",  # Princeton
            "12.161.14.0/24",  # Princeton
            "66.180.176.0/24",  # Princeton
            "66.180.177.0/24",  # Princeton
            "66.180.184.0/22",  # Princeton
            "132.177.251.17/32",  # UNH
        ]

        for cidr in allowed_cidrs:
            webserver_service.load_balancer.connections.allow_from(
                ec2.Peer.ipv4(cidr),
                ec2.Port.tcp(80),
            )
            webserver_service.load_balancer.connections.allow_from(
                ec2.Peer.ipv4(cidr),
                ec2.Port.tcp(443),
            )

        # Reduce the frequency of health checks
        webserver_service.target_group.configure_health_check(
            timeout=cdk.Duration.seconds(120),
            interval=cdk.Duration.seconds(300),
            unhealthy_threshold_count=3,
        )

        # Allow people to access the dagster webserver only
        # if they have LASP login credentials.
        # This is done by using OIDC authentication with the LASP Keycloak server.
        lasp_oidc_secret_name = "dagster_oidc_client"  # noqa: S105
        webserver_service.listener.add_action(
            "OidcAuthRule",
            priority=1,
            conditions=[elbv2.ListenerCondition.path_patterns(["/*"])],
            action=elbv2.ListenerAction.authenticate_oidc(
                authorization_endpoint=cdk.SecretValue.secrets_manager(
                    lasp_oidc_secret_name, json_field="authorization_endpoint"
                ).unsafe_unwrap(),
                token_endpoint=cdk.SecretValue.secrets_manager(
                    lasp_oidc_secret_name, json_field="token_endpoint"
                ).unsafe_unwrap(),
                user_info_endpoint=cdk.SecretValue.secrets_manager(
                    lasp_oidc_secret_name, json_field="user_info_endpoint"
                ).unsafe_unwrap(),
                issuer=cdk.SecretValue.secrets_manager(
                    lasp_oidc_secret_name, json_field="issuer"
                ).unsafe_unwrap(),
                client_id=cdk.SecretValue.secrets_manager(
                    lasp_oidc_secret_name, json_field="client_id_readwrite"
                ).unsafe_unwrap(),
                client_secret=cdk.SecretValue.secrets_manager(
                    lasp_oidc_secret_name, json_field="client_secret_readwrite"
                ),
                scope="openid profile",
                next=elbv2.ListenerAction.forward(
                    target_groups=[webserver_service.target_group]
                ),
            ),
        )

        ### Dagster Read Only Webserver
        readonly_webserver_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "DagsterReadonlyWebserver",
            cluster=cluster,
            cpu=4096,
            memory_limit_mib=8192,
            desired_count=1,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecr_image,
                command=[
                    "dagster-webserver",
                    "-h",
                    "0.0.0.0",
                    "-p",
                    "3000",
                    "-w",
                    "sds_data_manager/orchestration/workspace.yaml",
                    "--read-only",  # Makes it read only
                ],
                container_port=3000,
                environment=dagster_env_vars,
                execution_role=execution_role,
                task_role=task_role,
                log_driver=ecs.LogDriver.aws_logs(
                    stream_prefix="DagsterReadonlyWebserver",
                    log_group=logs.LogGroup(
                        self,
                        "DagsterReadonlyWebserverLogs",
                        removal_policy=RemovalPolicy.DESTROY,
                    ),
                ),
                secrets={
                    "DAGSTER_PG_PASSWORD": ecs.Secret.from_secrets_manager(
                        db_secret, "password"
                    )
                },
            ),
            public_load_balancer=True,
            open_listener=True,
            health_check_grace_period=cdk.Duration.seconds(300),
            certificate=certificate,
            domain_name=f"processing.{root_domain_name}",
            domain_zone=domain.hosted_zone,
            redirect_http=True,
        )

        # Allow the new read-only container to talk to the RDS database
        readonly_webserver_service.service.connections.allow_to(
            sg, ec2.Port.tcp(5432), "Allow Dagster Readonly Webserver to access RDS"
        )

        # Reduce the frequency of health checks
        readonly_webserver_service.target_group.configure_health_check(
            timeout=cdk.Duration.seconds(120),
            interval=cdk.Duration.seconds(300),
            unhealthy_threshold_count=3,
        )

        # First, only allow people to access the GraphQL API
        # if they have the correct header and value.
        readonly_webserver_service.listener.add_action(
            "ApiRule",
            priority=1,  # Lower number = higher priority.
            conditions=[
                # Only allow traffic that includes this exact header and value
                elbv2.ListenerCondition.http_header(
                    "x-dagster-api-key",
                    [
                        cdk.SecretValue.secrets_manager(
                            "dagster_graphql_api_key"
                        ).unsafe_unwrap()
                    ],
                )
            ],
            action=elbv2.ListenerAction.forward(
                target_groups=[readonly_webserver_service.target_group]
            ),
        )

        # Second, allow people to access the read-only webserver
        # if they have LASP login credentials.
        # This is done by using OIDC authentication with the LASP Keycloak server.
        readonly_webserver_service.listener.add_action(
            "OidcAuthRule",
            priority=2,
            conditions=[elbv2.ListenerCondition.path_patterns(["/*"])],
            action=elbv2.ListenerAction.authenticate_oidc(
                authorization_endpoint=cdk.SecretValue.secrets_manager(
                    lasp_oidc_secret_name, json_field="authorization_endpoint"
                ).unsafe_unwrap(),
                token_endpoint=cdk.SecretValue.secrets_manager(
                    lasp_oidc_secret_name, json_field="token_endpoint"
                ).unsafe_unwrap(),
                user_info_endpoint=cdk.SecretValue.secrets_manager(
                    lasp_oidc_secret_name, json_field="user_info_endpoint"
                ).unsafe_unwrap(),
                issuer=cdk.SecretValue.secrets_manager(
                    lasp_oidc_secret_name, json_field="issuer"
                ).unsafe_unwrap(),
                client_id=cdk.SecretValue.secrets_manager(
                    lasp_oidc_secret_name, json_field="client_id_readonly"
                ).unsafe_unwrap(),
                client_secret=cdk.SecretValue.secrets_manager(
                    lasp_oidc_secret_name, json_field="client_secret_readonly"
                ),
                scope="openid profile",
                next=elbv2.ListenerAction.forward(
                    target_groups=[readonly_webserver_service.target_group]
                ),
            ),
        )

        # Dagster Daemon
        daemon_task_def = ecs.FargateTaskDefinition(
            self,
            "DagsterDaemonTask",
            cpu=16384,
            memory_limit_mib=32768,
            execution_role=execution_role,
            task_role=task_role,
        )

        daemon_task_def.add_container(
            "DaemonContainer",
            image=ecr_image,
            command=[
                "dagster-daemon",
                "run",
                "-w",
                "sds_data_manager/orchestration/workspace.yaml",
            ],
            environment=dagster_env_vars,
            secrets={
                "DAGSTER_PG_PASSWORD": ecs.Secret.from_secrets_manager(
                    db_secret, "password"
                )
            },
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="DagsterDaemon",
                log_group=logs.LogGroup(
                    self, "DaemonLogs", removal_policy=RemovalPolicy.DESTROY
                ),
            ),
        )

        ecs.FargateService(
            self,
            "DagsterDaemonService",
            cluster=cluster,
            task_definition=daemon_task_def,
            desired_count=1,
            security_groups=[sg],
        )
