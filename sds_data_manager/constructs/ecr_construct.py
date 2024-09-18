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

        # TODO: remove these lines or find replacement. This is causing an error.
        # # Grant access to developers to push ECR Images to be used by the batch job
        # ecr_authenticators = iam.Group(self, "EcrAuthenticators")

        # # Allows members of this group to get the auth token for `docker login`
        # ecr.AuthorizationToken.grant_read(ecr_authenticators)

        # # Grant permissions to the group to pull and push images
        # self.container_repo.grant_pull_push(ecr_authenticators)

        # Add each of the SDC devs to the newly created group
        # TODO: should we remove this?
        # Error from this line:
        # The stack named loEcr failed creation, it may need to
        # be manually deleted from the AWS console: ROLLBACK_COMPLETE:
        # The maximum number of groups per user is exceeded for user: sandoval.
        # (Service: AmazonIdentityManagement; Status Code: 409; Error Code:
        # LimitExceeded; Request ID: 71ba3c0c-2be1-45e8-87cd-f1645fa49a94; Proxy: null)
        # for username in self.node.try_get_context("usernames"):
        #     user = iam.User.from_user_name(self, username, user_name=username)
        #     ecr_authenticators.add_user(user)

        self.container_repo.apply_removal_policy(RemovalPolicy.DESTROY)
