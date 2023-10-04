"""ECR Stack"""
from aws_cdk import Environment, RemovalPolicy, Stack
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
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
        self.container_repo = ecr.Repository(self, f"BatchRepository-{construct_id}",
                                             repository_name=f"{instrument_name.lower()}-repo",
                                             image_scan_on_push=True)

        # Grant access to developers to push ECR Images to be used by the batch job
        ecr_authenticators = iam.Group(self, 'EcrAuthenticators')

        # Allows members of this group to get the auth token for `docker login`
        ecr.AuthorizationToken.grant_read(ecr_authenticators)

        # Grant permissions to the group to pull and push images
        self.container_repo.grant_pull_push(ecr_authenticators)

        # Add each of the SDC devs to the newly created group
        for username in self.node.try_get_context("usernames"):
            user = iam.User.from_user_name(self, username, user_name=username)
            ecr_authenticators.add_user(user)

        self.container_repo.apply_removal_policy(RemovalPolicy.DESTROY)
