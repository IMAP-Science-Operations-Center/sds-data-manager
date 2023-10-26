"""SdpDatabase Stack"""
# Installed
import pathlib

import aws_cdk
from aws_cdk import (
    Environment,
    Stack,
    custom_resources,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import aws_lambda_python_alpha as lambda_alpha_
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
        sds_id: str,
        vpc: ec2.Vpc,
        rds_security_group,
        engine_version: rds.PostgresEngineVersion,
        instance_size: ec2.InstanceSize,
        instance_class: ec2.InstanceClass,
        max_allocated_storage: int,
        username: str,
        secret_name: str,
        database_name: str,
        **kwargs,
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

        self.secret_name = "sdp-database-creds-sdh-test"
        self.database_name = "imap"
        self.username = "imap_user"

        # Allow ingress to LASP IP address range and specific port
        rds_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            # peer=ec2.Peer.ipv4("128.138.131.0/24"),
            connection=ec2.Port.tcp(5432),
            description="Ingress RDS",
        )

        # Lambda was put into the same security group as the RDS, but we still need this
        rds_security_group.connections.allow_internally(
            ec2.Port.all_traffic(), description="Lambda ingress"
        )

        # Secrets manager credentials
        rds_creds = rds.DatabaseSecret(
            self, "RdsCredentials", secret_name=self.secret_name, username=username
        )

        # Subnets for RDS
        self.rds_subnet_selection = ec2.SubnetSelection(
            subnet_type=ec2.SubnetType.PUBLIC
        )

        # Define an IAM Role for RDS access
        iam.Role(
            self,
            "RDSDatabaseAccessRole",
            assumed_by=iam.ServicePrincipal("rds.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonRDSDataFullAccess"
                )
            ],
        )

        rds_instance = rds.DatabaseInstance(
            self,
            "RdsInstance",
            database_name=database_name,
            engine=rds.DatabaseInstanceEngine.postgres(version=engine_version),
            instance_type=ec2.InstanceType.of(instance_class, instance_size),
            vpc=vpc,
            vpc_subnets=self.rds_subnet_selection,
            credentials=rds.Credentials.from_secret(rds_creds),
            security_groups=[rds_security_group],
            publicly_accessible=True,
            max_allocated_storage=max_allocated_storage,
            deletion_protection=False,
        )

        self.host_name = rds_instance.instance_endpoint.hostname
        self.rds_region = rds_instance.instance_endpoint.socket_address.split(":")[0]

        schema_create_lambda = lambda_alpha_.PythonFunction(
            self,
            id="CreateMetadataSchema",
            function_name=f"create-schema-{sds_id}",
            entry=str(
                pathlib.Path(__file__).parent.joinpath("..", "lambda_code").resolve()
            ),
            index="SDSCode/create_schema.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=aws_cdk.Duration.minutes(15),
            memory_size=1000,
            environment={
                "HOST_NAME": self.host_name,
                "SECRET_NAME": self.secret_name,
                "DATABASE_NAME": database_name,
                "USERNAME": username,
                "REGION": self.rds_region,
            },
        )

        # Define the custom resource
        physical_resource_id = custom_resources.PhysicalResourceId.of(
            "create_schema_custom_resource"
        )
        custom_resources.AwsCustomResource(
            self,
            "SchemaCreationCustomResource",
            policy=custom_resources.AwsCustomResourcePolicy.from_statements(
                [
                    iam.PolicyStatement(
                        actions=["lambda:InvokeFunction"],
                        resources=[schema_create_lambda.function_arn],
                    )
                ]
            ),
            on_create=custom_resources.AwsSdkCall(
                action="lambda.invokeFunction",
                service="Lambda",
                parameters={"FunctionName": schema_create_lambda.function_name},
                physical_resource_id=physical_resource_id,
            ),
        )
