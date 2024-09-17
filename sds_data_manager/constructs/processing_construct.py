"""Processing construct for job scheduling/execution.

We will use batch processing to schedule and execute jobs. This construct
will create the necessary resources for batch processing.
"""

from typing import Optional

import aws_cdk as cdk
from aws_cdk import aws_batch as batch
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from constructs import Construct


class ProcessingConstruct(Construct):
    """Construct for processing jobs."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        volumes: Optional[list] = None,
        **kwargs,
    ):
        """Set up the primary processing environment and queue.

        Additional job definitions can be added to the construct later.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        vpc : ec2.Vpc
            VPC into which to launch the compute instance.
        volumes : list, optional
            List of volumes to attach to the compute instance, by default None.
        kwargs : dict
            Keyword arguments.
        """
        super().__init__(scope, construct_id, **kwargs)

        # Create compute environment
        compute_environment = batch.FargateComputeEnvironment(
            self,
            "ProcessingComputeEnvironment-spot",
            compute_environment_name="ProcessingComputeEnvironment-spot",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            spot=True,
        )

        # Create job queue
        self.job_queue = batch.JobQueue(
            self,
            "ProcessingJobQueue",
            job_queue_name="ProcessingJobQueue",
            compute_environments=[
                batch.OrderedComputeEnvironment(
                    compute_environment=compute_environment, order=1
                )
            ],
        )

        self.volumes = volumes

    def add_job(self, job_name: str):
        """Create an ECR repo and a job definition for the given job.

        Parameters
        ----------
        job_name : str
            Name of the job for which to create the job definition.
        """
        # Create a registry for each job definition (swe-repo)
        container_repo = ecr.Repository(
            self,
            f"ECR-{job_name}",
            repository_name=f"{job_name}-repo",
            empty_on_delete=True,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        # Create the job definition
        batch.EcsJobDefinition(
            self,
            f"ProcessingJob-{job_name}",
            job_definition_name=f"ProcessingJob-{job_name}",
            container=batch.EcsFargateContainerDefinition(
                self,
                f"FargateContainer-{job_name}",
                assign_public_ip=True,  # Required to pull ECR images
                image=ecs.ContainerImage.from_ecr_repository(
                    repository=container_repo, tag="latest"
                ),
                memory=cdk.Size.mebibytes(4096),
                cpu=1,
                environment={"IMAP_SPICE_DIR": "/mnt/spice"},
                volumes=self.volumes,
                # TODO: Do we need to explicitly specify architecture and OS family?
                #       We are building containers in GitHub Actions and need to
                #       make sure these are aligned.
                # fargate_cpu_architecture=ecs.CpuArchitecture.ARM64,
                # fargate_operating_system_family=ecs.OperatingSystemFamily.LINUX
            ),
        )
