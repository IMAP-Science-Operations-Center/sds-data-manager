"""Configure the AWS Batch resources.

This module provides the FargateBatchResources class which sets up AWS Batch
resources utilizing Fargate as the compute environment. The resources include:
  - IAM roles.
  - Compute environment for AWS Batch.
  - ECR repository for container images.
  - Batch job queue and job definition.
"""

from aws_cdk import Fn
from aws_cdk import aws_batch as batch
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secrets
from constructs import Construct

from sds_data_manager.stacks.efs_stack import EFSStack


class FargateBatchResources(Construct):
    """Fargate Batch compute env with named Job Queue, and Job Definition."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        processing_step_name: str,
        data_bucket: s3.Bucket,
        repo: ecr.Repository,
        db_secret_name: str,
        efs_instance: EFSStack,
        account_name: str,
        batch_max_vcpus=10,
        job_vcpus=0.25,
        job_memory=2048,
        **kwargs,
    ):
        """Construct the fargate batch resources.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        vpc : ec2.Vpc
            VPC into which to launch the compute instance.
        processing_step_name : str
            Name of data product being generated in this Batch job.
        data_bucket : s3.Bucket
            S3 bucket.
        repo : ecr.Repository
            Container repo
        db_secret_name : str
            RDS secret name for secret manager access
        efs_instance: efs.Filesystem
            EFS stack object
        account_name: str
            account name such as 'dev' or 'prod' or user specified.
            account_name is used as ECR's tag.
            This value can be overwritten by command line input and can
            be accessed from the cdk.json file.
        batch_max_vcpus : int, Optional
            Maximum number of virtual CPUs per compute instance.
        job_vcpus : int, Optional
            Number of virtual CPUs required per Batch job.
            Dependent on Docker image contents.
        job_memory : int: Optional
            Memory required per Batch job in MiB. Dependent on Docker image contents.
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        self.role = iam.Role(
            self,
            "BatchServiceRole",
            assumed_by=iam.ServicePrincipal("batch.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSBatchServiceRole"
                )
            ],
        )

        # Required since our task is hosted on AWS Fargate,
        # is pulling container images from the ECR, and sending
        # container logs to CloudWatch.
        fargate_execution_role = iam.Role(
            self,
            "FargateExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )

        # Setup a security group for the Fargate-generated EC2 instances.
        batch_security_group = ec2.SecurityGroup(
            self, "FargateInstanceSecurityGroup", vpc=vpc
        )

        # PRIVATE_WITH_NAT allows batch job to pull images from the ECR.
        # TODO: Evaluate SPOT resources
        self.compute_environment = batch.CfnComputeEnvironment(
            self,
            "FargateBatchComputeEnvironment",
            type="MANAGED",
            service_role=self.role.role_arn,
            compute_resources=batch.CfnComputeEnvironment.ComputeResourcesProperty(
                type="FARGATE",
                maxv_cpus=batch_max_vcpus,
                subnets=vpc.select_subnets(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_NAT
                ).subnet_ids,
                security_group_ids=[batch_security_group.security_group_id],
            ),
        )

        # The set of compute environments mapped to a job queue
        # and their order relative to each other
        compute_environment_order = batch.CfnJobQueue.ComputeEnvironmentOrderProperty(
            compute_environment=self.compute_environment.ref, order=1
        )

        repo.grant_pull_push(fargate_execution_role)

        # Setup job queue
        self.job_queue_name = f"{processing_step_name}-fargate-batch-job-queue"
        self.job_queue = batch.CfnJobQueue(
            self,
            "FargateBatchJobQueue",
            job_queue_name=self.job_queue_name,
            priority=1,
            compute_environment_order=[compute_environment_order],
        )

        # Batch job role, so we can later grant access to the appropriate
        # S3 buckets and other resources
        self.batch_job_role = iam.Role(
            self,
            "BatchJobRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonElasticFileSystemFullAccess"
                ),
            ],
        )
        data_bucket.grant_read_write(self.batch_job_role)
        # Add RDS DB access to the batch job
        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", db_secret_name
        )
        rds_secret.grant_read(grantee=self.batch_job_role)

        # TODO: come back and add ability to grab latest version of
        # processing_step_name tag. I think this will require
        # setting up a lambda. Maybe there's another way?
        self.job_definition_name = f"fargate-batch-job-definition{processing_step_name}"

        self.job_definition = batch.CfnJobDefinition(
            self,
            "FargateBatchJobDefinition",
            job_definition_name=self.job_definition_name,
            type="CONTAINER",
            platform_capabilities=["FARGATE"],
            container_properties={
                "image": f"{repo.repository_uri}:latest",
                "environment": [
                    {"name": "IMAP_SPICE_DIR", "value": "/mnt/spice"},
                ],
                "mountPoints": [
                    {
                        "sourceVolume": efs_instance.volume_name,
                        "containerPath": "/mnt/spice",
                        "readOnly": False,
                    }
                ],
                "volumes": [
                    {
                        "name": efs_instance.volume_name,
                        "efsVolumeConfiguration": {
                            "fileSystemId": Fn.import_value(
                                efs_instance.efs_fs_id_name
                            ),
                            "rootDirectory": "/",
                            "transitEncryption": "ENABLED",
                            "transitEncryptionPort": 2049,
                            "authorizationConfig": {
                                "accessPointId": Fn.import_value(
                                    efs_instance.spice_access_point_id_name
                                ),
                                "iam": "ENABLED",
                            },
                        },
                    }
                ],
                "resourceRequirements": [
                    {"value": str(job_memory), "type": "MEMORY"},
                    {"value": str(job_vcpus), "type": "VCPU"},
                ],
                "executionRoleArn": fargate_execution_role.role_arn,
                "jobRoleArn": self.batch_job_role.role_arn,
            },
            tags={"Purpose": "Batch Processing"},
        )
