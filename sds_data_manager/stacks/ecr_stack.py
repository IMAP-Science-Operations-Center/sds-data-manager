"""ECR Stack"""
from aws_cdk import Environment, RemovalPolicy, Stack
from aws_cdk import aws_ecr as ecr
from constructs import Construct


class EcrStack(Stack):
    """Ecr Resources"""

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 instrument_name: str,
                 env: Environment,
                 **kwargs) -> None:
        """DataStorageStack constructor

        Parameters
        ----------
        scope : App
        construct_id : str
        instrument_name : str
            Name of instrument
        env : Environment
            Account and region
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        # Define registry for storing processing docker images
        #TODO: have different repos for instruments but not levels
        #TODO: keep it private for now, but may be public later
        self.container_repo = ecr.Repository(self, f"BatchRepository-{construct_id}",
                                             repository_name=f"{instrument_name.lower()}-repo",
                                             image_scan_on_push=True)

        self.container_repo.apply_removal_policy(RemovalPolicy.DESTROY)
