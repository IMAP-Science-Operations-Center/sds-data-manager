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
from aws_cdk import aws_ssm as ssm
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

        # Set up secure reference to batch API key parameter name
        # The actual key value is not resolved at synthesis time for security
        # NOTE: To use this, create the key using the helper script:
        #
        #   python sds_data_manager/lambda_code/authorization/manage_api_keys.py \
        #     add "batch-jobs" "imap-sdc@lists.lasp.colorado.edu"
        #
        # Then add that API Key to SSM as a separate parameter the ECS task can look up
        #
        #  aws ssm put-parameter --name /imap-sdc/batch-jobs/api-key --value <the-key> \
        #    --type SecureString
        #
        self.batch_secret_api_key = batch.Secret.from_ssm_parameter(
            ssm.StringParameter.from_secure_string_parameter_attributes(
                self,
                "BatchApiKeyParam",
                # Location in parameter store where the batch API key is stored
                parameter_name="/imap-sdc/batch-jobs/api-key",
            )
        )

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

    def add_job(self, job_name: str, data_access_url: str = ""):
        """Create an ECR repo and a job definition for the given job.

        Parameters
        ----------
        job_name : str
            Name of the job for which to create the job definition.
        data_access_url : str, optional
            The data access URL to use for this job, by default the empty string.
            You should set this to the appropriate API endpoint, e.g.
            https://api.dev.imap-mission.com/api-key
        """
        # Create a registry for each job definition (swe-repo)
        container_repo = ecr.Repository(
            self,
            f"ECR-{job_name}",
            repository_name=f"{job_name}-repo",
            empty_on_delete=True,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        # Ultra and Lo jobs need more resources than others. Specifically, ULTRA and
        # Lo l1a
        # TODO : optimize ULTRA and Lo l1a to use less resources
        cpu = 4
        memory = cdk.Size.gibibytes(16)
        if "ultra" in job_name or "lo" in job_name:
            cpu = 8
            memory = cdk.Size.gibibytes(48)
        # Create the job definition
        container_definition = batch.EcsFargateContainerDefinition(
            self,
            f"FargateContainer-{job_name}",
            assign_public_ip=True,  # Required to pull ECR images
            image=ecs.ContainerImage.from_ecr_repository(
                repository=container_repo, tag="latest"
            ),
            memory=memory,
            cpu=cpu,
            environment={
                # Useful for switching APIs between dev / prod endpoints
                "IMAP_DATA_ACCESS_URL": data_access_url,
            },
            # Use Batch secrets to securely inject the API key from SSM
            # This ensures the key is not visible in CloudFormation templates
            secrets={"IMAP_API_KEY": self.batch_secret_api_key},
            # TODO: Do we need to explicitly specify architecture and OS family?
            #       We are building containers in GitHub Actions and need to
            #       make sure these are aligned.
            # fargate_cpu_architecture=ecs.CpuArchitecture.ARM64,
            # fargate_operating_system_family=ecs.OperatingSystemFamily.LINUX
        )

        batch.EcsJobDefinition(
            self,
            f"ProcessingJob-{job_name}",
            job_definition_name=f"ProcessingJob-{job_name}",
            container=container_definition,
            timeout=cdk.Duration.minutes(150),
        )
