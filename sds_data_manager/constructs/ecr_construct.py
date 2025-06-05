"""Configure the ECR Construct."""

from aws_cdk import RemovalPolicy
from aws_cdk import aws_ecr as ecr
from constructs import Construct


class EcrConstruct(Construct):
    """Construct the ECR Resources."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        instrument_name: str,
        **kwargs,
    ) -> None:
        """DataStorageConstruct constructor.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        instrument_name : str
            Name of instrument
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        # Define registry for storing processing docker images
        self.container_repo = ecr.Repository(
            self,
            f"BatchRepository-{construct_id}",
            repository_name=f"{instrument_name.lower()}-repo",
            image_scan_on_push=True,
        )

        self.container_repo.apply_removal_policy(RemovalPolicy.RETAIN)
