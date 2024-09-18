"""Configure the database."""

import aws_cdk as cdk
from aws_cdk import CustomResource
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secrets
from aws_cdk import custom_resources as cr
from constructs import Construct


class SdpDatabase(Construct):
    """Construct for creating database."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        engine_version: rds.PostgresEngineVersion,
        instance_size: ec2.InstanceSize,
        instance_class: ec2.InstanceClass,
        max_allocated_storage: int,
        username: str,
        secret_name: str,
        database_name: str,
        code: lambda_.Code,
        layers: list,
        **kwargs,
    ) -> None:
        """Database construct.

        Parameters
        ----------
        scope : Construct
            The App object in which to create this Construct
        construct_id : str
            The ID (name) of the stack
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
        code : lambda_.Code
            Lambda code bundle to create the initial DB schema
        layers : list
            List of Lambda layers to attach to the lambda function
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        self.secret_name = secret_name

        self.rds_security_group = ec2.SecurityGroup(
            scope, "RdsSecurityGroup", vpc=vpc, allow_all_outbound=True
        )
        # Allow ingress to LASP IP address range and specific port
        self.rds_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("128.138.131.0/24"),
            connection=ec2.Port.tcp(5432),
            description="Ingress RDS",
        )

        # Lambda was put into the same security group as the RDS, but we still need this
        # TODO: Is this still needed? We get a warning in the CDK logs with it
        #       because allow_all_outbound is set to True already.
        # self.rds_security_group.connections.allow_internally(
        #     ec2.Port.all_traffic(), description="Lambda ingress"
        # )

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
        self.rds_creds.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        # Subnets for RDS
        self.rds_subnet_selection = ec2.SubnetSelection(
            subnet_type=ec2.SubnetType.PUBLIC
        )

        db = rds.DatabaseInstance(
            self,
            "RdsInstance",
            database_name=database_name,
            engine=rds.DatabaseInstanceEngine.postgres(version=engine_version),
            instance_type=ec2.InstanceType.of(instance_class, instance_size),
            vpc=vpc,
            vpc_subnets=self.rds_subnet_selection,
            credentials=rds.Credentials.from_secret(self.rds_creds),
            security_groups=[self.rds_security_group],
            publicly_accessible=True,
            max_allocated_storage=max_allocated_storage,
            deletion_protection=False,
        )

        # NOTE: Specifically, to report success or failure, have your Lambda Function
        #       exit in the right way: return data for success, or throw an exception
        #       for failure. Do not post the success or failure of your custom resource
        #       to an HTTPS URL as the CloudFormation documentation tells you to do.
        #       https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.custom_resources/README.html
        schema_create_lambda = lambda_.Function(
            self,
            id="CreateMetadataSchema",
            function_name="create-schema",
            code=code,
            handler="SDSCode.create_schema.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.seconds(60),
            memory_size=1000,
            allow_public_subnet=True,
            vpc=vpc,
            vpc_subnets=self.rds_subnet_selection,
            security_groups=[self.rds_security_group],
            environment={
                "SECRET_NAME": self.secret_name,
            },
            layers=layers,
            architecture=lambda_.Architecture.ARM_64,
        )
        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", self.secret_name
        )
        rds_secret.grant_read(grantee=schema_create_lambda)
        db.connections.allow_from(schema_create_lambda, ec2.Port.tcp(5432))

        res_provider = cr.Provider(
            self, "crProvider", on_event_handler=schema_create_lambda
        )
        db_custom_resource = CustomResource(
            self, "CustomResource-DB-Schema", service_token=res_provider.service_token
        )
        # Add an explicit dependency on the RDS instance because we need the secret
        # populated with the DB credentials before we can create the schema.
        db_custom_resource.node.add_dependency(db)
