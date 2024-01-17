"""Processing Stack
This is the module containing the general stack to be built for
computation of I-ALiRT algorithms
"""
from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from constructs import Construct

from sds_data_manager.constructs.ialirt_compute_resources import (
    IalirtEC2Resources,
)


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

        self.vpc = vpc
        self.repo = repo
        self.add_compute_resources()
        self.add_dynamodb_table()

    # Setup the EC2 resources
    def add_compute_resources(self):
        """Add EC2 components"""
        self.ec2_resources = IalirtEC2Resources(
            self,
            "IalirtEC2Environment",
            vpc=self.vpc,
            repo=self.repo,
        )

    # I-ALiRT IOIS DynamoDB
    # ingest-ugps: ingestion ugps - 64 bit
    # sct-vtcw: spacecraft time ugps - 64 bit
    # src-seq-ctr: increments with each packet (included in filename?)
    # ccsds-filename: filename of the packet
    def add_dynamodb_table(self):
        """Add DynamoDB Table here"""
        dynamodb.Table(
            self,
            "DynamoDB-ialirt",
            table_name="ialirt-packets",
            partition_key=dynamodb.Attribute(
                name="ingest-time", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="spacecraft-time", type=dynamodb.AttributeType.STRING
            ),
            # on-demand
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=True,
        )
