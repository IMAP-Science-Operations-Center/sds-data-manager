"""SdpDatabase Stack"""
# Installed

from aws_cdk import Environment, Stack
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_rds as rds,
)
from constructs import Construct


class SdpDatabase(Stack):
    """Stack for creating database"""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env: Environment,
        vpc: ec2.Vpc,
        rds_security_group,
        engine_version: rds.PostgresEngineVersion,
        instance_size: ec2.InstanceSize,
        instance_class: ec2.InstanceClass,
        max_allocated_storage: int,
        username: str,
        secret_name: str,
        database_name: str,
        **kwargs
    ) -> None:
        """
        Parameters
        ----------
        scope : Construct
            The App object in which to create this Stack
        construct_id : str
            The ID (name) of the stack
        env : Environment
            CDK environment
        vpc : ec2.Vpc
            Virtual private cloud
        engine_version : rds.PostgresEngineVersion
            Version of postgres database to use
        instance_size : ec2.InstanceSize
            Instance size for ec2
        instance_class : ec2.InstanceClass
            Instance class for ec2
        max_allocated_storage : int
            Upper limit to which RDS can scale the storage in GiB
        username : str,
            Database username
        secret_name : str,
            Database secret_name for Secrets Manager
        database_name : str,
            Database name
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        self.secret_name = secret_name

        # Allow ingress to LASP IP address range and specific port
        rds_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("128.138.131.0/24"),
            connection=ec2.Port.tcp(5432),
            description="Ingress RDS",
        )

        # Lambda was put into the same security group as the RDS, but we still need this
        rds_security_group.connections.allow_internally(
            ec2.Port.all_traffic(), description="Lambda ingress"
        )

        # Secrets manager credentials
        # NOTE:
        # If credentials already exists, then we will need to delete
        # the secret before recreating it.
        #
        # Use this command with <> value replaced:
        # aws --profile <aws profile> secretsmanager delete-secret \
        # --secret-id <secret name> \
        # --force-delete-without-recovery
        self.rds_creds = rds.DatabaseSecret(
            self, "RdsCredentials", secret_name=self.secret_name, username=username
        )

        # Subnets for RDS
        self.rds_subnet_selection = ec2.SubnetSelection(
            subnet_type=ec2.SubnetType.PUBLIC
        )

        rds.DatabaseInstance(
            self,
            "RdsInstance",
            database_name=database_name,
            engine=rds.DatabaseInstanceEngine.postgres(version=engine_version),
            instance_type=ec2.InstanceType.of(instance_class, instance_size),
            vpc=vpc,
            vpc_subnets=self.rds_subnet_selection,
            credentials=rds.Credentials.from_secret(self.rds_creds),
            security_groups=[rds_security_group],
            publicly_accessible=True,
            max_allocated_storage=max_allocated_storage,
            deletion_protection=False,
        )
