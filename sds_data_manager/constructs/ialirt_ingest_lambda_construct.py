"""Configure the ialirt ingest lambda construct."""

import aws_cdk as cdk
from aws_cdk import RemovalPolicy, aws_dynamodb, aws_s3
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_efs as efs
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class IalirtIngestLambda(Construct):
    """Construct for ialirt ingest lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        ialirt_bucket: aws_s3.Bucket,
        vpc: ec2.Vpc,
        efs_access_point: efs.AccessPoint,
        docker_path: str = "sds_data_manager/lambda_code",
        data_access_url: str = "",
        account_name: str = "dev",
        **kwargs,
    ) -> None:
        """IalirtIngestLambda Stack.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        ialirt_bucket : aws_s3.Bucket
            The data bucket.
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        efs_access_point: efs.AccessPoint
            EFS access point to mount inside the Lambda function.
        docker_path : str
            Path to the Dockerfile.
        data_access_url : str, optional
            The data access URL to use for this job, by default the empty string.
            You should set this to the appropriate API endpoint, e.g.
            https://api.dev.imap-mission.com
        account_name : str
            The account name. Eg. 'prod' or 'dev'
        kwargs : dict
            Keyword arguments.

        """
        super().__init__(scope, construct_id, **kwargs)

        # EFS resources
        self.efs_access_point = efs_access_point
        self.vpc = vpc
        self.account_name = account_name

        # Create DynamoDB Table
        self.algorithm_data_table = self.create_algorithm_dynamodb_table()
        self.data_table = self.create_table()

        # Create Lambda Function
        self.ialirt_ingest_lambda = self.create_lambda_function(
            ialirt_bucket,
            self.algorithm_data_table,
            self.data_table,
            docker_path,
            data_access_url,
        )

        # Create Event Rule
        self.create_event_rule(ialirt_bucket, self.ialirt_ingest_lambda)

    def create_algorithm_dynamodb_table(self) -> aws_dynamodb.Table:
        """Create and return the algorithm data product table."""
        removal_policy = (
            RemovalPolicy.RETAIN
            if self.account_name == "prod"
            else RemovalPolicy.DESTROY
        )
        point_in_time_recovery = True if self.account_name == "prod" else False

        self.algorithm_data_table = ddb.Table(
            self,
            "IalirtAlgorithmDataTable",
            table_name="ialirt-algorithm-table",
            # RemovalPolicy.RETAIN to keep the table after stack deletion.
            removal_policy=removal_policy,
            # Restore data to any point in time within the last 35 days.
            point_in_time_recovery=point_in_time_recovery,
            # Partition key (PK) = APID.
            partition_key=ddb.Attribute(
                name="apid",
                type=ddb.AttributeType.NUMBER,
            ),
            # Sort key (SK) = Mission Elapsed Time (MET).
            sort_key=ddb.Attribute(
                name="met",
                type=ddb.AttributeType.NUMBER,
            ),
            # Define the read and write capacity units.
            # TODO: change to provisioned capacity mode in production.
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,  # On-Demand capacity mode.
        )

        # Add a GSI for ingest time.
        self.algorithm_data_table.add_global_secondary_index(
            index_name="met_in_utc",
            # Partition key (PK) = APID.
            partition_key=ddb.Attribute(name="apid", type=ddb.AttributeType.NUMBER),
            # Sort key (SK) = Insert Time (ISO).
            sort_key=ddb.Attribute(
                name="met_in_utc",
                type=ddb.AttributeType.STRING,
            ),
            projection_type=ddb.ProjectionType.ALL,
        )

        # Add a GSI for instrument product name.
        self.algorithm_data_table.add_global_secondary_index(
            index_name="last_modified",
            # Partition key (PK) = APID.
            partition_key=ddb.Attribute(name="apid", type=ddb.AttributeType.NUMBER),
            # Sort key (SK) = Instrument product name.
            sort_key=ddb.Attribute(
                name="last_modified",
                type=ddb.AttributeType.STRING,
            ),
            projection_type=ddb.ProjectionType.ALL,
        )
        return self.algorithm_data_table

    def create_table(self) -> aws_dynamodb.Table:
        """Create and return the algorithm data product table."""
        removal_policy = (
            RemovalPolicy.RETAIN
            if self.account_name == "prod"
            else RemovalPolicy.DESTROY
        )
        point_in_time_recovery = True if self.account_name == "prod" else False

        self.data_table = ddb.Table(
            self,
            "IalirtDataTable",
            table_name="ialirt-data-table",
            # RemovalPolicy.RETAIN to keep the table after stack deletion.
            removal_policy=removal_policy,
            # Restore data to any point in time within the last 35 days.
            point_in_time_recovery=point_in_time_recovery,
            # Partition key (PK) = instrument.
            partition_key=ddb.Attribute(
                name="instrument",
                type=ddb.AttributeType.STRING,
            ),
            # Sort key (SK) = time_utc.
            sort_key=ddb.Attribute(
                name="time_utc",
                type=ddb.AttributeType.STRING,
            ),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,  # On-Demand capacity mode.
        )

        return self.data_table

    def create_lambda_function(
        self,
        ialirt_bucket: aws_s3.Bucket,
        algorithm_data_table: aws_dynamodb.Table,
        data_table: aws_dynamodb.Table,
        docker_path: str,
        data_access_url: str,
    ) -> lambda_.DockerImageFunction:
        """Create and return the Lambda function."""
        lambda_role = iam.Role(
            self,
            "IalirtIngestLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonDynamoDBFullAccess"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
            ],
        )

        s3_read_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:ListBucket", "s3:GetObject"],
            resources=[
                ialirt_bucket.bucket_arn,
                f"{ialirt_bucket.bucket_arn}/*",
            ],
        )

        ialirt_ingest_lambda = lambda_.DockerImageFunction(
            self,
            id="IalirtIngestLambda",
            code=lambda_.DockerImageCode.from_image_asset(
                docker_path,
                file="IAlirtCode/Dockerfile.ingest",
            ),
            function_name="ialirt-ingest",
            timeout=cdk.Duration.minutes(15),
            memory_size=1000,
            role=lambda_role,
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            filesystem=lambda_.FileSystem.from_efs_access_point(
                self.efs_access_point, "/mnt/data"
            ),
            environment={
                "ALGORITHM_TABLE": algorithm_data_table.table_name,
                "DATA_TABLE": data_table.table_name,
                "S3_BUCKET": ialirt_bucket.bucket_name,
                "EFS_SPICE_MOUNT_PATH": "/mnt/data",
                "IMAP_DATA_ACCESS_URL": data_access_url,
            },
        )
        ialirt_ingest_lambda.add_to_role_policy(s3_read_policy)
        algorithm_data_table.grant_read_write_data(ialirt_ingest_lambda)
        data_table.grant_read_write_data(ialirt_ingest_lambda)

        # The resource is deleted when the stack is deleted.
        ialirt_ingest_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        return ialirt_ingest_lambda

    def create_event_rule(
        self,
        ialirt_bucket: aws_s3.Bucket,
        ialirt_ingest_lambda: lambda_.DockerImageFunction,
    ) -> None:
        """Create the event rule to trigger Lambda on S3 object creation."""
        ialirt_data_arrival_rule = events.Rule(
            self,
            "IalirtDataArrival",
            rule_name="ialirt-data-arrival",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [ialirt_bucket.bucket_name]},
                    "object": {"key": [{"prefix": "packets/"}]},
                },
            ),
        )

        # Add the Lambda function as the target for the rules
        ialirt_data_arrival_rule.add_target(
            targets.LambdaFunction(ialirt_ingest_lambda)
        )
