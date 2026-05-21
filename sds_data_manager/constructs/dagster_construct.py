#!/usr/bin/env python3

from constructs import Construct
from aws_cdk import (
    RemovalPolicy,
    Stack,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
    aws_ecs_patterns as ecs_patterns,
    aws_ecr as ecr,
)
from aws_cdk.aws_ecr_assets import DockerImageAsset, Platform
from aws_cdk.aws_ecr import Repository
from cdk_ecr_deployment import ECRDeployment, DockerImageName

import aws_cdk as cdk


class ElasticContainerRegistryStack(Stack):
    """Create the ECR to store the container images."""

    def __init__(self, scope, id, *, repo_name, untagged_image_duration, **kwargs):
        super().__init__(scope, id, **kwargs)

        repo_lifecycle_rule = ecr.LifecycleRule(
            description="Remove old untagged images",
            max_image_age=cdk.Duration.days(untagged_image_duration),
            tag_status=ecr.TagStatus.UNTAGGED,
        )

        self.repo = ecr.Repository(
            self, id, lifecycle_rules=[repo_lifecycle_rule], repository_name=repo_name
        )


class DockerImageStack(Stack):
    """Create the Docker image and push it to ECR."""

    def __init__(self, scope, id, *, image_name, directory, file="Dockerfile", ecr,
                 docker_tag="latest", **kwargs):
        super().__init__(scope, id, **kwargs)

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


class DagsterEcsStack(Stack):
    """ECS Fargate stack running the Dagster webserver."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        env_vars: dict,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ECS Cluster
        cluster = ecs.Cluster(self, "DagsterCluster", vpc=vpc)

        security_group = ec2.SecurityGroup(
            self,
            "DagsterSecurityGroup",
            vpc=vpc,
            description="Security group for Dagster ECS tasks",
            allow_all_outbound=True,
        )

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
            self, construct_id, repository_name="dagster-image"
        )
        ecr_image = ecs.EcrImage(dagster_repo, "latest")

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
                    "dagster",
                    "dev",
                    "-h",
                    "0.0.0.0",
                    "-p",
                    "3000",
                    "-w",
                    "orchestration/workspace.yaml",
                ],
                container_port=3000,
                environment=env_vars,
                execution_role=execution_role,
                task_role=task_role,
                log_driver=ecs.LogDriver.aws_logs(
                    stream_prefix="DagsterWebserver",
                    log_group=logs.LogGroup(
                        self,
                        "WebserverLogs",
                        removal_policy=RemovalPolicy.DESTROY,
                    ),
                ),
            ),
            public_load_balancer=True,
            # Set to False for VPN/Internal access
            open_listener=False,
            security_groups=[security_group],
        )
        webserver_service.load_balancer.connections.allow_from(
            ec2.Peer.ipv4("128.138.131.0/24"),
            ec2.Port.tcp(80),
        )