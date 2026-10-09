"""Configure the ialirt SSR processing construct."""

from aws_cdk import RemovalPolicy, aws_dynamodb
from aws_cdk import aws_dynamodb as ddb
from constructs import Construct


class IalirtSsrConstruct(Construct):
    """Construct for ialirt SSR processing resources."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        account_name: str = "dev",
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
        kwargs : dict
            Keyword arguments.

        """
        super().__init__(scope, construct_id, **kwargs)

        self.account_name = account_name

        # Create DynamoDB Table
        self.ssr_table = self.create_table()

    def create_table(self) -> aws_dynamodb.Table:
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

        return self.ssr_table
