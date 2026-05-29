#!/usr/bin/env python3

from constructs import Construct
from aws_cdk import (
    App,
    Stack,
    RemovalPolicy,
    aws_s3 as s3,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_rds as rds,
    aws_iam as iam,
    aws_logs as logs,
    aws_ecs_patterns as ecs_patterns,
    aws_secretsmanager as secretsmanager,
)
from aws_cdk.aws_ecr_assets import DockerImageAsset, Platform
from aws_cdk.aws_ecr import Repository
from cdk_ecr_deployment import ECRDeployment, DockerImageName
from aws_cdk import Stack

import aws_cdk as cdk


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

        repo_lifecycle_rule = ecr.LifecycleRule(description='Remove old untagged images',
                                                max_image_age=cdk.Duration.days(7), # Remove after 7 days
                                                tag_status=ecr.TagStatus.UNTAGGED)

        self.container_repo = ecr.Repository(self, construct_id, lifecycle_rules=[repo_lifecycle_rule],
                                            repository_name=repo_name)

        self.container_repo.apply_removal_policy(RemovalPolicy.RETAIN)

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
    """Construct the database Dagster needs to keep track of processing state"""

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
            self, "DagsterDatabaseSecret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username":"dagster"}',
                generate_string_key="password",
                exclude_characters="\"@/\\"
            )
        )
        
        self.db_instance = rds.DatabaseInstance(
            self, "DagsterStorageDB",
            engine=rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_16),
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.XLARGE2),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            credentials=rds.Credentials.from_secret(self.db_secret),
            database_name="dagster",
            security_groups=[sg],
            removal_policy=RemovalPolicy.RETAIN, # Retain data on stack updates/destroy
        )

class DagsterS3LoggingBucket(Construct):
    """Construct the database Dagster needs to store logs from asset runs"""

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

        self.logs_bucket = s3.Bucket(self, "DagsterComputeLogsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL
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
        **kwargs,
    ) -> None:
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
            self, "DagsterRunBaseTaskDef",
            cpu=1024, 
            memory_limit_mib=2048,
            execution_role=execution_role,
            task_role=task_role
        )
        run_task_def.add_container(
            "dagster-run", # Must match standard naming expectation
            image=ecr_image,
            environment=dagster_env_vars,
            secrets={"DAGSTER_PG_PASSWORD": ecs.Secret.from_secrets_manager(db_secret, "password")},
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="DagsterRuns",
                log_group=logs.LogGroup(self, "RunLogs", removal_policy=RemovalPolicy.DESTROY)
            )
        )
        dagster_env_vars["DAGSTER_RUN_BASE_TASK_DEF_ARN"] = run_task_def.task_definition_arn
        
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
                secrets={"DAGSTER_PG_PASSWORD": ecs.Secret.from_secrets_manager(db_secret, "password")},
            ),
            public_load_balancer=True,
            open_listener=False,
        )
        webserver_service.load_balancer.connections.allow_from(
            ec2.Peer.ipv4("128.138.131.0/24"),
            ec2.Port.tcp(80),
        )
        webserver_service.service.connections.allow_to(
            sg,
            ec2.Port.tcp(5432),
            "Allow Dagster Webserver to access RDS"
        )

        # Dagster Daemon
        daemon_task_def = ecs.FargateTaskDefinition(self, "DagsterDaemonTask",
            cpu=16384,
            memory_limit_mib=32768,
            execution_role=execution_role,
            task_role=task_role
        )
        
        daemon_task_def.add_container("DaemonContainer",
            image=ecr_image,
            command=["dagster-daemon", "run", "-w", "sds_data_manager/orchestration/workspace.yaml"],
            environment=dagster_env_vars,
            secrets={
                "DAGSTER_PG_PASSWORD": ecs.Secret.from_secrets_manager(db_secret, "password")
            },
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="DagsterDaemon",
                log_group=logs.LogGroup(self, "DaemonLogs", removal_policy=RemovalPolicy.DESTROY)
            )
        )

        ecs.FargateService(self, "DagsterDaemonService",
            cluster=cluster,
            task_definition=daemon_task_def,
            desired_count=1,
            security_groups=[sg]
        )