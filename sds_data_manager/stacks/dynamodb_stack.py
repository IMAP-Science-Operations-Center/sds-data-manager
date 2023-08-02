# Create stack for DynamoDB
from typing import Optional

from aws_cdk import Environment, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct


class DynamoDB(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        sds_id: str,
        table_name: str,
        partition_key: str,
        sort_key: str,
        env: Environment,
        on_demand: bool = True,
        read_capacity: Optional[int] = None,
        write_capacity: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)
        """
        Parameters
        ----------
        scope : Construct
        construct_id : str
        sds_id : str
            Name suffix for stack
        table_name : str
            Database table name
        partition_key : str
            Partition key for DynamoDB table. The partition key must be unique within a
            table. Every item in a DynamoDB table is uniquely identified by its
            partition key value. DynamoDB uses the partition key to distribute the
            data across multiple partitions for scalability and performance.

            When performing operations such as PutItem, GetItem, or Query in DynamoDB,
            you need to provide a unique value for the partition key to uniquely
            identify the item. If you attempt to insert an item with a partition key
            value that already exists in thetable, it will overwrite the existing
            item with the new data.

            If we want partition key to be not unique, then we need to make sure
            combination of partition key and sort key is unique.

        sort_key : str
            the sort key (also known as the range key) does not have to be unique
            within a partition. Unlike the partition key, the sort key can have
            duplicate values within a partition.

            The combination of the partition key and sort key together must be
            unique for each item in a DynamoDB table. This means that while multiple
            items within a partition can have the same sort key value, their partition
            key values must be different.
        env : Environment
            Account and region
        on_demand : bool
            If true, creates on demand DynamoDB table. If false, creates provisioned
            DynamoDB table.
        read_capacity : int
            Read capacity for provisioned DynamoDB table.
            Default value is 1.
        write_capacity : int
            Write capacity for provisioned DynamoDB table.
            Default value is 1.
        """
        self.sds_id = sds_id
        self.table_name = table_name
        self.partition_key = partition_key
        self.sort_key = sort_key
        self.on_demand = on_demand

        if not on_demand and read_capacity is None and write_capacity is None:
            raise ValueError(
                "Required parameters read_capacity and write_capacity are not set"
            )

        if on_demand:
            # On Demand DynamoDB table is created with PAY_PER_REQUEST billing mode.
            billing_mode = dynamodb.BillingMode.PAY_PER_REQUEST
        else:
            # Provisioned DynamoDB table is created with PROVISIONED billing mode.
            billing_mode = dynamodb.BillingMode.PROVISIONED

        # When you turn on point-in-time recovery (PITR), DynamoDB backs up your table
        # data automatically so that you can restore to any given second in the
        # preceding 35 days
        dynamodb.Table(
            self,
            f"DynamoDB-{self.sds_id}",
            table_name=self.table_name,
            partition_key=dynamodb.Attribute(
                name=self.partition_key, type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name=self.sort_key, type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            write_capacity=write_capacity,
            read_capacity=read_capacity,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=True,
        )
