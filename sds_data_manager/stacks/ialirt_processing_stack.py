"""Processing Stack
This is the module containing the general stack to be built for
computation of I-ALiRT algorithms
"""
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from constructs import Construct

from sds_data_manager.constructs.ialirt_compute_resources import (
    IalirtEC2Resources,
)
from sds_data_manager.stacks import ialirt_dynamodb_stack


class IalirtProcessing(Stack):
    """A processing system for ialirt."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        repo: ecr.Repository,
        **kwargs,
    ) -> None:
        """Constructor

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
        """
        super().__init__(scope, construct_id, **kwargs)

        # Setup the EC2 resources
        self.ec2_resources = IalirtEC2Resources(
            self,
            "IalirtEC2Environment",
            vpc=vpc,
            repo=repo,
        )

        # I-ALiRT IOIS DynamoDB
        # ingest-ugps: ingestion ugps - 64 bit
        # sct-vtcw: spacecraft time ugps - 64 bit
        # src-seq-ctr: increments with each packet (included in filename?)
        # ccsds-filename: filename of the packet
        ialirt_dynamodb_stack.DynamoDB(
            scope,
            construct_id="IalirtDynamoDB",
            table_name="ialirt-iois",
            partition_key="ingest-ugps",
            sort_key="sct-vtcw",
            on_demand=True,
            # TODO: set read_capacity and write_capacity
        )
