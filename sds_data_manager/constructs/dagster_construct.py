#!/usr/bin/env python3

# source $(poetry env info --path)/bin/activate
# cdk deploy --require-approval never --app "python3 app_dagster_dev.py" --all --profile dev --account_name="dev"
# aws ecs update-service --cluster DagsterTestStack-DagsterCluster8836562F-bJqwwNMSfe64 --service DagsterTestStack-DagsterWebserverServiceC3A572F4-vjP4xSJR0Zdq --force-new-deployment --profile dev

from constructs import Construct
from aws_cdk import (
    App,
    Stack,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
    aws_ecs_patterns as ecs_patterns,
)
from aws_cdk import Stack
from aws_cdk.aws_ecr_assets import DockerImageAsset, Platform
from cdk_ecr_deployment import ECRDeployment, DockerImageName

from aws_cdk import aws_ecr as ecr
from aws_cdk import Stack

import aws_cdk as cdk
from aws_cdk.aws_ecr import Repository


class ElasticContainerRegistryStack(Stack):
    """Create the ECR to store the container images.
    """

    def __init__(self, scope, id, *, repo_name, untagged_image_duration, **kwargs):
        super().__init__(scope, id, **kwargs)

        repo_lifecycle_rule = ecr.LifecycleRule(
            description='Remove old untagged images',
            max_image_age=cdk.Duration.days(untagged_image_duration),
            tag_status=ecr.TagStatus.UNTAGGED)

        self.repo = ecr.Repository(self, id, lifecycle_rules=[repo_lifecycle_rule],
                                   repository_name=repo_name)


class DockerImageStack(Stack):
    """Create the ECR to store the container images.
    """

    def __init__(self, scope, id, *, image_name, directory, file='Dockerfile', ecr,
                 docker_tag='latest', **kwargs):
        super().__init__(scope, id, **kwargs)

        self.asset = DockerImageAsset(self,
                                      image_name + "_image",
                                      directory=directory,
                                      file=file,
                                      platform=Platform.LINUX_AMD64
                                      )

        self.image = ECRDeployment(self,
                                   image_name + "_copy",
                                   src=DockerImageName(self.asset.image_uri),
                                   dest=DockerImageName(ecr + ":" + docker_tag),
                                   memory_limit=4096
                                   )


class DagsterEcsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # VPC Configuration
        vpc = ec2.Vpc.from_lookup(self, "IMAPVPC", vpc_id="vpc-0830f37f150973fc2")

        # ECS Cluster
        cluster = ecs.Cluster(self, "DagsterCluster", vpc=vpc)

        # IAM Roles for Tasks
        # Execution role for pulling images and writing logs
        execution_role = iam.Role(self, "DagsterExecutionRole",
                                  assumed_by=iam.ServicePrincipal(
                                      "ecs-tasks.amazonaws.com"),
                                  managed_policies=[
                                      iam.ManagedPolicy.from_aws_managed_policy_name(
                                          "service-role/AmazonECSTaskExecutionRolePolicy")]
                                  )

        # Task role
        task_role = iam.Role(self, "DagsterTaskRole",
                             assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
                             )
        # TODO: Terrible idea for now
        task_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess"))

        dagster_repo = Repository.from_repository_name(self, construct_id,
                                                       repository_name="dagster-image")
        ecr_image = ecs.EcrImage(dagster_repo, "latest")

        dagster_env_vars = {
            "S3_BUCKET": "sds-data-449431850278",
            "SECRET_NAME": "sdp-database-cred",
            "ACCOUNT": "449431850278",
            "REGION": "us-west-2",
            "IMAP_DATA_ACCESS_URL": "https://api.dev.imap-mission.com",
            "SSM_API_KEY_PARAMETER": "/imap-sdc/batch-jobs/api-key"
        }

        # NOTE: Had to add rules to this.
        sg = ec2.SecurityGroup.from_security_group_id(
            self,
            "ImportedSG",
            "sg-0c09351ddecbc082e",
            mutable=False
        )

        webserver_service = ecs_patterns.ApplicationLoadBalancedFargateService(self,
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
                                                                                       "orchestration/workspace.yaml"],
                                                                                   container_port=3000,
                                                                                   environment=dagster_env_vars,
                                                                                   execution_role=execution_role,
                                                                                   task_role=task_role,
                                                                                   log_driver=ecs.LogDriver.aws_logs(
                                                                                       stream_prefix="DagsterWebserver",
                                                                                       log_group=logs.LogGroup(
                                                                                           self,
                                                                                           "WebserverLogs",
                                                                                           removal_policy=RemovalPolicy.DESTROY)
                                                                                   )
                                                                               ),
                                                                               public_load_balancer=True,
                                                                               # Set to False for VPN/Internal access
                                                                               open_listener=False,
                                                                               security_groups=[
                                                                                   sg]
                                                                               )
        webserver_service.load_balancer.connections.allow_from(
            ec2.Peer.ipv4("128.138.131.0/24"),
            ec2.Port.tcp(80)
        )


app = App()
cdk_env = cdk.Environment(account="449431850278", region="us-west-2")
dagster_repo_stack = ElasticContainerRegistryStack(app,
                                                   'DagsterImageECR',
                                                   repo_name="dagster-image",
                                                   untagged_image_duration=7,
                                                   env=cdk_env
                                                   )

dagster_image_stack = DockerImageStack(app, "DagsterImageStack",
                                       image_name="DagsterImage", directory='.',
                                       file='Dockerfile',
                                       ecr=dagster_repo_stack.repo.repository_uri,
                                       env=cdk_env)
dagster_fargate_cluster = DagsterEcsStack(app, "DagsterTestStack", env=cdk_env)
dagster_fargate_cluster.node.add_dependency(dagster_image_stack)
app.synth()