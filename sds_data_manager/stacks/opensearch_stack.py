# Installed
from constructs import Construct
from aws_cdk import (
    Stack,
    Environment,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_opensearchservice as opensearch,
    aws_secretsmanager as secretsmanager,
    RemovalPolicy
)


class OpenSearch(Stack):
    """Creates OpenSearch cluster and policies."""
    def __init__(self, scope: Construct,
                 construct_id: str,
                 sds_id: str,
                 env: Environment,
                 **kwargs) -> None:
        """
        Parameters
        ----------
        scope : Construct
        construct_id : str
        sds_id : str
            Name suffix for stack
        env : Environment
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        # Create a secret username/password for OpenSearch
        self.os_secret = secretsmanager.Secret(self, f"OpenSearchPassword-{sds_id}")

        # Create the opensearch cluster
        self.sds_metadata_domain = opensearch.Domain(
            self,
            f"SDSMetadataDomain-{sds_id}",
            domain_name=f"sdsmetadatadomain-{sds_id}",
            version=opensearch.EngineVersion.OPENSEARCH_1_3,
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
