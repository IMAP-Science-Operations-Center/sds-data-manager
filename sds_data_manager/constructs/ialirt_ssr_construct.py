"""Configure the ialirt SSR processing construct."""

import aws_cdk as cdk
from aws_cdk import RemovalPolicy
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_ssm as ssm
from constructs import Construct

# One Lambda per instrument, plus spacecraft status and position/velocity.
SSR_INSTRUMENTS = [
    "mag",
    "hit",
    "swe",
    "codice_lo",
    "codice_hi",
    "swapi",
    "spacecraft_status",
    "spacecraft",
]


class IalirtSsrConstruct(Construct):
    """Construct for ialirt SSR processing resources."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        account_name: str,
        vpc: ec2.Vpc,
        docker_path: str = "sds_data_manager/lambda_code",
        data_access_url: str = "",
        **kwargs,
    ) -> None:
        """IalirtSsrConstruct Stack.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        account_name : str
            The account name. Eg. 'prod' or 'dev'
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        docker_path : str
            Path to the Dockerfile.
        data_access_url : str, optional
            The data access URL, e.g. https://api.dev.imap-mission.com
        kwargs : dict
            Keyword arguments.

        """
        super().__init__(scope, construct_id, **kwargs)

        self.account_name = account_name
        self.vpc = vpc

        # Create DynamoDB Table
        self.ssr_table = self.create_table()

        # Create one Lambda per instrument
        self.ssr_lambdas = self.create_lambda_functions(docker_path, data_access_url)

        # Create the Lambda that invokes the instrument Lambdas per day
        self.dispatch_lambda = self.create_dispatch_lambda(docker_path)

        # Create the daily and manual triggers
        self.create_event_rules()

    def create_table(self) -> ddb.Table:
        """Create and return the SSR data product table."""
        removal_policy = (
            RemovalPolicy.RETAIN
            if self.account_name == "prod"
            else RemovalPolicy.DESTROY
        )
        point_in_time_recovery = True if self.account_name == "prod" else False

        self.ssr_table = ddb.Table(
            self,
            "IalirtSsrTable",
            table_name="ialirt-ssr-table",
            # RemovalPolicy.RETAIN to keep the table after stack deletion.
            removal_policy=removal_policy,
            # Restore data to any point in time within the last 35 days.
            point_in_time_recovery=point_in_time_recovery,
            # Partition key (PK) = instrument and version, e.g. "mag#v001".
            # Including the version lets each version be stored and
            # queried separately instead of overwriting the previous one.
            partition_key=ddb.Attribute(
                name="instrument_version",
                type=ddb.AttributeType.STRING,
            ),
            # Sort key (SK) = time_utc.
            sort_key=ddb.Attribute(
                name="time_utc",
                type=ddb.AttributeType.STRING,
            ),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,  # On-Demand capacity mode.
        )

        return self.ssr_table

    def create_lambda_functions(
        self,
        docker_path: str,
        data_access_url: str,
    ) -> dict[str, lambda_.DockerImageFunction]:
        """Create one Lambda per instrument from a shared image."""
        lambda_role = iam.Role(
            self,
            "IalirtSsrLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
            ],
        )
        self.ssr_table.grant_read_write_data(lambda_role)

        # Unreleased L0 files are only returned to queries with an API key.
        api_key_parameter = ssm.StringParameter.from_secure_string_parameter_attributes(
            self,
            "ApiKeyParameter",
            parameter_name="/imap-sdc/batch-jobs/api-key",
        )
        api_key_parameter.grant_read(lambda_role)

        code = lambda_.DockerImageCode.from_image_asset(
            docker_path,
            file="IAlirtCode/Dockerfile.ssr",
        )

        ssr_lambdas = {}
        for instrument in SSR_INSTRUMENTS:
            ssr_lambda = lambda_.DockerImageFunction(
                self,
                id=f"IalirtSsrLambda-{instrument}",
                code=code,
                function_name=f"ialirt-ssr-{instrument.replace('_', '-')}",
                timeout=cdk.Duration.minutes(15),
                # Estimated peak is ~600-900 MB (parsing two days of packets).
                # 2048 MB also gives more than one vCPU.
                memory_size=2048,
                # Packets (~29 MB for day + next day), SPICE kernels
                # (de440.bsp alone is ~114 MB) and ancillary files are
                # downloaded to /tmp on every invocation.
                ephemeral_storage_size=cdk.Size.gibibytes(2),
                role=lambda_role,
                vpc=self.vpc,
                vpc_subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                ),
                environment={
                    "INSTRUMENT": instrument,
                    "SSR_TABLE": self.ssr_table.table_name,
                    "IMAP_DATA_ACCESS_URL": data_access_url,
                    "SSM_API_KEY_PARAMETER": "/imap-sdc/batch-jobs/api-key",
                },
                # Limits how many days of one instrument run at once during
                # a backfill, so SSR does not take concurrency from
                # ialirt-ingest and the SDC APIs. 8 x 20 = 160 reserved.
                # Throttled async invocations are retried for up to 6 hours.
                reserved_concurrent_executions=20,
            )
            ssr_lambda.apply_removal_policy(RemovalPolicy.DESTROY)
            ssr_lambdas[instrument] = ssr_lambda

        return ssr_lambdas

    def create_dispatch_lambda(self, code_path: str) -> lambda_.Function:
        """Create the Lambda that invokes the instrument Lambdas per day."""
        dispatch_lambda = lambda_.Function(
            self,
            "IalirtSsrDispatchLambda",
            function_name="ialirt-ssr-dispatch",
            runtime=lambda_.Runtime.PYTHON_3_12,
            code=lambda_.Code.from_asset(code_path),
            handler="IAlirtCode.ialirt_ssr_dispatch.lambda_handler",
            timeout=cdk.Duration.minutes(15),
        )
        for ssr_lambda in self.ssr_lambdas.values():
            ssr_lambda.grant_invoke(dispatch_lambda)
        dispatch_lambda.apply_removal_policy(RemovalPolicy.DESTROY)
        return dispatch_lambda

    def create_event_rules(self) -> None:
        """Create the daily and manual triggers for the dispatch Lambda."""
        daily_rule = events.Rule(
            self,
            "IalirtSsrDailyRule",
            rule_name="ialirt-ssr-daily",
            schedule=events.Schedule.cron(minute="0", hour="6"),
        )
        daily_rule.add_target(targets.LambdaFunction(self.dispatch_lambda))

        # Manual trigger: put a custom event on the default event bus, e.g.
        # Source "imap.ialirt.ssr", DetailType "SSR Processing Request",
        # Detail {"start_date": "20260901", "end_date": "20260910"}.
        manual_rule = events.Rule(
            self,
            "IalirtSsrManualRule",
            rule_name="ialirt-ssr-manual",
            event_pattern=events.EventPattern(
                source=["imap.ialirt.ssr"],
                detail_type=["SSR Processing Request"],
            ),
        )
        manual_rule.add_target(targets.LambdaFunction(self.dispatch_lambda))
