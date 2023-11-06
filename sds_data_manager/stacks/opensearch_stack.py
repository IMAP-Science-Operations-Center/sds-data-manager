# Installed
from aws_cdk import (
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_opensearchservice as opensearch,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class OpenSearch(Stack):
    """Creates OpenSearch cluster and policies."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs,
    ) -> None:
        """
        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        """
        super().__init__(scope, construct_id, **kwargs)

        # Define Database name related constants
        self.secret_name = "sdp-database-creds"

        # Create a secret username/password for OpenSearch
        self.os_secret = secretsmanager.Secret(
            self, "OpenSearchPassword", secret_name=self.secret_name
        )

        # Create the opensearch cluster
        self.sds_metadata_domain = opensearch.Domain(
            self,
            "SDSMetadataDomain",
            domain_name="sdsmetadatadomain",
            version=opensearch.EngineVersion.OPENSEARCH_2_7,
            capacity=opensearch.CapacityConfig(
                data_nodes=1,
                data_node_instance_type="t3.small.search",
            ),
            ebs=opensearch.EbsOptions(
                volume_size=10,
                volume_type=ec2.EbsDeviceVolumeType.GP2,
            ),
            logging=opensearch.LoggingOptions(
                slow_search_log_enabled=True,
                app_log_enabled=True,
                slow_index_log_enabled=True,
            ),
            node_to_node_encryption=True,
            encryption_at_rest=opensearch.EncryptionAtRestOptions(enabled=True),
            enforce_https=True,
            removal_policy=RemovalPolicy.DESTROY,
            fine_grained_access_control=opensearch.AdvancedSecurityOptions(
                master_user_name="master-user",
                master_user_password=self.os_secret.secret_value,
            ),
        )

        # add an access policy for opensearch
        self.sds_metadata_domain.add_access_policies(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.AnyPrincipal()],
                actions=["es:*"],
                resources=[self.sds_metadata_domain.domain_arn + "/*"],
            )
        )

        # IAM policies
        self.opensearch_all_http_permissions = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["es:ESHttp*"],
            resources=[f"{self.sds_metadata_domain.domain_arn}/*"],
        )

        self.opensearch_read_only_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["es:ESHttpGet"],
            resources=[f"{self.sds_metadata_domain.domain_arn}/*"],
        )
