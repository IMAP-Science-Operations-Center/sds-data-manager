"""Configure the indexer lambda."""

import aws_cdk as cdk
from aws_cdk import Environment
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secrets
from constructs import Construct
from imap_data_access import VALID_INSTRUMENTS


class IndexerLambda(Construct):
    """Construct for indexer lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        code: lambda_.Code,
        db_secret_name: str,
        vpc: ec2.Vpc,
        vpc_subnets,
        rds_security_group,
        data_bucket,
        layers: list,
        **kwargs,
    ) -> None:
        """IndexerLambda Construct.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        code : aws_lambda.Code
            Lambda code bundle
        db_secret_name : str
            The DB secret name
        vpc : obj
            The VPC
        vpc_subnets : obj
            The VPC subnets
        rds_security_group : obj
            The RDS security group
        data_bucket : obj
            The data bucket
        layers : list
            List of Lambda layers cdk.cdfnOutput names
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        indexer_lambda = lambda_.Function(
            self,
            id="IndexerLambda",
            function_name="file-indexer",
            code=code,
            handler="SDSCode.pipeline_lambdas.indexer.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            allow_public_subnet=True,
            vpc=vpc,
            vpc_subnets=vpc_subnets,
            security_groups=[rds_security_group],
            environment={
                "DATA_TRACKER_INDEX": "data_tracker",
                "S3_BUCKET": data_bucket.bucket_name,
                "SECRET_NAME": db_secret_name,
            },
            layers=layers,
        )

        # Adding events and s3 permission because indexer
        # lambda sents events and read from s3.
        # TODO: narrow s3 permission later
        put_event_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["events:PutEvents", "s3:*"],
            resources=[
                "*",
            ],
        )

        indexer_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
        indexer_lambda.add_to_role_policy(put_event_policy)

        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", db_secret_name
        )
        rds_secret.grant_read(grantee=indexer_lambda)

        # Events that triggers Indexer Lambda:
        # 1. Arrival of all science data
        # 2. PutEvent from Lambda that builds dependency and starts Batch Job
        # 3. Batch Job status change

        # Write science data info to db with
        # status SUCCEEDED
        science_event_prefixes = [
            {"prefix": f"imap/{instrument}/"} for instrument in VALID_INSTRUMENTS
        ]
        science_event_prefixes.append({"prefix": "imap/ancillary/"})
        science_event_prefixes.append({"prefix": "imap/quicklook/"})
        imap_data_arrival_rule = events.Rule(
            self,
            "ImapDataArrival",
            rule_name="imap-data-arrival",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [data_bucket.bucket_name]},
                    "object": {
                        "key": science_event_prefixes,
                    },
                },
            ),
        )

        # Uses batch job status
        # to update status in the database and
        # update ingested time if status was success
        batch_job_status_rule = events.Rule(
            self,
            "batchJobStatus",
            rule_name="batch-job-status",
            event_pattern=events.EventPattern(
                source=["aws.batch"],
                detail_type=["Batch Job State Change"],
                detail={"status": ["SUCCEEDED", "FAILED"]},
            ),
        )

        # Add the Lambda function as the target for the rules
        imap_data_arrival_rule.add_target(targets.LambdaFunction(indexer_lambda))
        batch_job_status_rule.add_target(targets.LambdaFunction(indexer_lambda))


class SPICEIndexerLambda(Construct):
    """Construct for the SPICE Indexer Lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        code: lambda_.Code,
        db_secret_name: str,
        env: Environment,
        vpc: ec2.Vpc,
        layers: list,
        rds_security_group,
        data_bucket: s3.Bucket,
        data_access_url: str = "",
        **kwargs,
    ) -> None:
        """Construct the EFS lambdas.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        code : aws_lambda.Code
            Lambda code bundle
        db_secret_name : str
            The DB secret name
        env : Environment
            Account and region
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        layers : list
            List of Lambda layers cdk.cdfnOutput names
        rds_security_group : obj
            The RDS security group
        data_bucket : obj
            The data bucket
        data_access_url : str, optional
            The data access URL to use for this job, by default the empty string.
            You should set this to the appropriate API endpoint, e.g.
            https://api.dev.imap-mission.com
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        # Create a role for the SPICE lambda
        # Grant the Lambda identity role access to the VPC/EFS
        iam_role_name = "spice-lambda-role"
        efs_lambda_role = iam.Role(
            self,
            iam_role_name,
            role_name=iam_role_name,
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "SecretsManagerReadWrite"
                ),
            ],
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )

        self.spice_ingest_lambda = lambda_.Function(
            self,
            "SPICEIndexerLambda",
            function_name="spice-file-indexer",
            # Allow access to the EFS over NFS port
            # allow_all_outbound=True,
            runtime=lambda_.Runtime.PYTHON_3_12,
            code=code,
            handler="SDSCode.pipeline_lambdas.spice_indexer.lambda_handler",
            role=efs_lambda_role,
            description="""Lambda that writes SPICE files to the EFS and indexes
                           them in our database.""",
            # Access to the EFS requires to be within the VPC
            vpc=vpc,
            timeout=cdk.Duration.minutes(1),
            architecture=lambda_.Architecture.X86_64,
            layers=layers,
            memory_size=1000,
            security_groups=[rds_security_group],
            environment={
                "IMAP_DATA_DIR": "/tmp",  # noqa: S108
                "IMAP_DATA_ACCESS_URL": data_access_url,
                "SECRET_NAME": db_secret_name,
                "S3_BUCKET": data_bucket.bucket_name,
            },
        )

        # Adding events and s3 permission because indexer
        # lambda sents events and read from s3 respectively.
        put_event_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["events:PutEvents", "s3:*"],
            resources=[
                "*",
            ],
        )
        self.spice_ingest_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
        self.spice_ingest_lambda.add_to_role_policy(put_event_policy)

        rds_secret = secrets.Secret.from_secret_name_v2(
            self, "rds_secret", db_secret_name
        )
        rds_secret.grant_read(grantee=self.spice_ingest_lambda)

        # Define an EventBridge rule
        event_rule = events.Rule(
            self,
            "SPICEIndexerLambdaS3EventRule",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [data_bucket.bucket_name]},
                    "object": {
                        "key": [
                            {"prefix": "imap/spice/"},
                        ]
                    },
                },
            ),
        )

        # Add the Lambda function as the target for the rule
        event_rule.add_target(targets.LambdaFunction(self.spice_ingest_lambda))
