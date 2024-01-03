"""Processing Stack
This is the module containing the general stack to be built for
computation of I-ALiRT algorithms
"""
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
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
        ecr_policy: iam.PolicyStatement,
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
        ecr_policy : iam.PolicyStatement
            ECR policy statement.
        """
        super().__init__(scope, construct_id, **kwargs)

        # Setup the EC2 resources
        self.ec2_resources = IalirtEC2Resources(
            self,
            "IalirtEC2Environment",
            vpc=vpc,
            ecr_policy=ecr_policy,
            repo=repo,
        )
