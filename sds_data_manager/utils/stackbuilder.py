"""Module with helper functions for creating standard sets of stacks"""
from pathlib import Path

from aws_cdk import App, Environment
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds

from sds_data_manager.stacks import (
    api_gateway_stack,
    backup_bucket_stack,
    create_schema_stack,
    database_stack,
    domain_stack,
    dynamodb_stack,
    ecr_stack,
    networking_stack,
    opensearch_stack,
    processing_stack,
    sds_data_manager_stack,
    step_function_stack,
)


def build_sds(
    scope: App,
    env: Environment,
    account_config: dict,
    rds_size: str = "SMALL",
    rds_class: str = "BURSTABLE3",
    rds_storage: int = 200,
    use_custom_domain: bool = False,
):
    """Builds the entire SDS

    Parameters
    ----------
    scope : Construct
        Parent construct.
    env : Environment
        Account and region
    account_config : dict
        Account configuration (domain_name and other account specific configurations)
    """
    open_search = opensearch_stack.OpenSearch(scope, "OpenSearch", env=env)

    dynamodb = dynamodb_stack.DynamoDB(
        scope,
        construct_id="DynamoDB",
        table_name="data-watcher",
        partition_key="instrument",
        sort_key="filename",
        env=env,
    )

    # TODO: discuss taking components of this to conform to
    # other step function processing steps
    processing_step_function = step_function_stack.ProcessingStepFunctionStack(
        scope,
        "ProcessingStepFunctionStack",
        dynamodb_table_name=dynamodb.table_name,
        env=env,
    )

    domain = None
    domain_name = account_config.get("domain_name", None)
    if domain_name is not None:
        domain = domain_stack.DomainStack(
            scope,
            "DomainStack",
            domain_name=domain_name,
            account_name=account_config["account_name"],
            env=env,
        )

    networking = networking_stack.NetworkingStack(scope, "Networking", env=env)

    rds_stack = database_stack.SdpDatabase(
        scope,
        "RDS",
        description="IMAP SDP database.",
        env=env,
        vpc=networking.vpc,
        rds_security_group=networking.rds_security_group,
        engine_version=rds.PostgresEngineVersion.VER_15_2,
        instance_size=ec2.InstanceSize[rds_size],
        instance_class=ec2.InstanceClass[rds_class],
        max_allocated_storage=rds_storage,
        username="postgres",
        secret_name="sdp-database-creds",
        database_name="imapdb",
    )

    data_manager = sds_data_manager_stack.SdsDataManager(
        scope,
        "SdsDataManager",
        open_search,
        dynamodb,
        processing_step_function_arn=processing_step_function.sfn.state_machine_arn,
        env=env,
        db_secret_name=rds_stack.secret_name,
        vpc=networking.vpc,
        vpc_subnets=rds_stack.rds_subnet_selection,
        rds_security_group=networking.rds_security_group,
    )

    api_gateway_stack.ApiGateway(
        scope,
        "ApiGateway",
        data_manager.lambda_functions,
        domain_stack=domain,
        env=env,
    )

    instrument_list = ["Codice"]  # etc

    lambda_code_directory = Path(__file__).parent.parent / "lambda_code" / "SDSCode"
    lambda_code_directory_str = str(lambda_code_directory.resolve())

    for instrument in instrument_list:
        ecr = ecr_stack.EcrStack(
            scope,
            f"{instrument}Processing",
            env=env,
            instrument_name=f"{instrument}",
        )

        processing_stack.ProcessingStep(
            scope,
            f"L1b{instrument}Processing",
            env=env,
            vpc=networking.vpc,
            processing_step_name=f"l1b-{instrument}",
            lambda_code_directory=lambda_code_directory_str,
            data_bucket=data_manager.data_bucket,
            instrument_target=f"l1b_{instrument}",
            instrument_sources=f"l1a_{instrument}",
            repo=ecr.container_repo,
            batch_security_group=networking.batch_security_group,
            rds_security_group=networking.rds_security_group,
            subnets=rds_stack.rds_subnet_selection,
            db_secret_name=rds_stack.rds_creds.secret_arn,
        )

        processing_stack.ProcessingStep(
            scope,
            f"L1c{instrument}Processing",
            env=env,
            vpc=networking.vpc,
            processing_step_name=f"l1c-{instrument}",
            lambda_code_directory=lambda_code_directory_str,
            data_bucket=data_manager.data_bucket,
            instrument_target=f"l1c_{instrument}",
            instrument_sources=f"l1b_{instrument}",
            repo=ecr.container_repo,
            batch_security_group=networking.batch_security_group,
            rds_security_group=networking.rds_security_group,
            subnets=rds_stack.rds_subnet_selection,
            db_secret_name=rds_stack.rds_creds.secret_arn,
        )

        create_schema_stack.CreateSchema(
            scope,
            "CreateSchemaStack",
            env=env,
            db_secret_name=rds_stack.secret_name,
            vpc=networking.vpc,
            vpc_subnets=rds_stack.rds_subnet_selection,
            rds_security_group=networking.rds_security_group,
        )


def build_backup(scope: App, env: Environment, source_account: str):
    """Builds backup bucket with permissions for replication from source_account.

    Parameters
    ----------
    scope : Construct
        Parent construct.
    env : Environment
        Account and region
    source_account : str
        Account number for source bucket for replication
    """
    # This is the S3 bucket used by upload_api_lambda
    backup_bucket_stack.BackupBucket(
        scope,
        "BackupBucket",
        source_account=source_account,
        env=env,
    )
