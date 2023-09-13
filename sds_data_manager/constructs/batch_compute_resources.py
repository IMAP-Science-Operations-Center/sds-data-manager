from constructs import Construct
from aws_cdk import (
    aws_batch as batch,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_ecr as ecr,
    aws_secretsmanager as secrets
)


class FargateBatchResources(Construct):
    """Fargate Batch compute environment with named Job Queue, and Job Definition
    """

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 sds_id: str,
                 vpc: ec2.Vpc,
                 processing_step_name: str,
                 security_group: classmethod = None,
                 job_vcpus=0.25,
                 job_memory=512):
        """Constructor

        Parameters
        ----------
        scope : Construct
        construct_id : str
        vpc : ec2.Vpc
            VPC into which to launch the compute instance.
        processing_step_name : str
            Name of data product being generated in this Batch job.
        security_group : classmethod, Optional
            Batch processing security group to access the RDS. If provided, must have an ingress rule to the RDS
            security group.
        db_secret_name : str, Optional
            Set if the batch job needs access to the database.
        batch_has_db_access : bool, Optional
            Set to True if the batch job needs to access the database.
        batch_max_vcpus : int, Optional
            Maximum number of virtual CPUs per compute instance.
        job_vcpus : int, Optional
            Number of virtual CPUs required per Batch job. Dependent on Docker image contents.
        job_memory : int: Optional
            Memory required per Batch job in MB. Dependent on Docker image contents.
        """
        super().__init__(scope, construct_id)

        # Makes calls to other AWS services to manage resources used with AWS Batch.
        self.role = iam.Role(self, f"BatchServiceRole-{sds_id}",
                             assumed_by=iam.ServicePrincipal('batch.amazonaws.com'),
                             managed_policies=[
                                 iam.ManagedPolicy.from_aws_managed_policy_name(
                                     "service-role/AWSBatchServiceRole")
                             ]
                             )

        # Required since our task is hosted on AWS Fargate, is pulling container images from the ECR, and sending
        # container logs to CloudWatch.
        fargate_execution_role = iam.Role(self, f"FargateExecutionRole-{sds_id}",
                                          assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
                                          managed_policies=[
                                              iam.ManagedPolicy.from_aws_managed_policy_name(
                                                  'service-role/AmazonECSTaskExecutionRolePolicy')
                                          ])

        # AWS Batch manages AWS Fargate resources based on our specifications.
        # PRIVATE_WITH_NAT allows batch job to pull images from the ECR.
        self.compute_environment = batch.CfnComputeEnvironment(
            self, f"FargateBatchComputeEnvironment-{sds_id}",
            type='MANAGED',
            service_role=self.role.role_arn,
            compute_resources=batch.CfnComputeEnvironment.ComputeResourcesProperty(
                type='FARGATE',
                subnets=vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_WITH_NAT).subnet_ids,
                security_group_ids=[security_group.security_group_id]
            )
        )

        # The set of compute environments mapped to a job queue and their order relative to each other
        compute_environment_order = batch.CfnJobQueue.ComputeEnvironmentOrderProperty(
            compute_environment=self.compute_environment.ref,
            order=1)

        # Define registry for storing processing docker images
        # Uses the pre-built AWS construct to build the container repository
        # The repo is named specific to this data product
        self.container_registry = ecr.Repository(self, f"BatchRepository-{sds_id}",
                                                 repository_name=f"{processing_step_name}-docker-repo",
                                                 image_scan_on_push=True)

        # Setup job queue
        self.job_queue_name = f"{processing_step_name}-fargate-batch-job-queue"
        self.job_queue = batch.CfnJobQueue(self, f"FargateBatchJobQueue-{sds_id}",
                                           job_queue_name=self.job_queue_name,
                                           priority=1,
                                           compute_environment_order=[compute_environment_order])

        # Batch job role so we can later grant access to the appropriate S3 buckets and other resources
        self.batch_job_role = iam.Role(self, f"BatchJobRole-{sds_id}",
                                       assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
                                       managed_policies=[
                                           iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess")])

        # create job definition
        self.job_definition_name = f"fargate-batch-job-definition{processing_step_name}"
        self.job_definition = batch.CfnJobDefinition(
            self, f"FargateBatchJobDefinition-{sds_id}",
            job_definition_name=self.job_definition_name,
            type="CONTAINER",
            platform_capabilities=['FARGATE'],
            container_properties={
                'image': self.container_registry.repository_uri,
                'resourceRequirements': [
                    {
                        'value': str(job_memory),
                        'type': 'MEMORY'
                    },
                    {
                        'value': str(job_vcpus),
                        'type': 'VCPU'
                    }
                ],
                'executionRoleArn': fargate_execution_role.role_arn,
                'jobRoleArn': self.batch_job_role.role_arn,
            },
        )
