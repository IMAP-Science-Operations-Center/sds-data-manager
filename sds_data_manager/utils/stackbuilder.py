"""Module with helper functions for creating standard sets of stacks."""

from pathlib import Path

import imap_data_access
from aws_cdk import App, Environment
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds

from sds_data_manager.stacks import (
    api_gateway_stack,
    backup_bucket_stack,
    batch_compute_resources,
    create_schema_stack,
    data_bucket_stack,
    database_stack,
    domain_stack,
    ecr_stack,
    efs_stack,
    ialirt_processing_stack,
    indexer_lambda_stack,
    instrument_lambdas,
    monitoring_stack,
    networking_stack,
    sds_api_manager_stack,
)


def build_sds(
    scope: App,
    env: Environment,
    account_config: dict,
):
    """Build the entire SDS.

    Parameters
    ----------
    scope : Construct
        Parent construct.
    env : Environment
        Account and region
    account_config : dict
        Account configuration (domain_name and other account specific configurations)

    """
    data_bucket = data_bucket_stack.DataBucketStack(
        scope=scope, construct_id="DataBucket", env=env
    )

    networking = networking_stack.NetworkingStack(scope, "Networking", env=env)

    monitoring = monitoring_stack.MonitoringStack(
        scope=scope,
        construct_id="MonitoringStack",
        env=env,
    )

    domain = None
    domain_name = account_config.get("domain_name", None)
    account_name = account_config["account_name"]
    if domain_name is not None:
        domain = domain_stack.DomainStack(
            scope,
            "DomainStack",
            domain_name=domain_name,
            account_name=account_name,
            env=env,
        )

    api = api_gateway_stack.ApiGateway(
        scope,
        "ApiGateway",
        domain_stack=domain,
        env=env,
    )
    api.deliver_to_sns(monitoring.sns_topic_notifications)

    # Get RDS properties from account_config
    rds_size = account_config.get("rds_size", "SMALL")
    rds_class = account_config.get("rds_class", "BURSTABLE3")
    rds_storage = account_config.get("rds_stack", 200)
    db_secret_name = "sdp-database-cred"  # noqa
    rds_stack = database_stack.SdpDatabase(
        scope,
        "RDS",
        description="IMAP SDP database.",
        env=env,
        vpc=networking.vpc,
        rds_security_group=networking.rds_security_group,
        engine_version=rds.PostgresEngineVersion.VER_15_6,
        instance_size=ec2.InstanceSize[rds_size],
        instance_class=ec2.InstanceClass[rds_class],
        max_allocated_storage=rds_storage,
        username="imap_user",
        secret_name=db_secret_name,
        database_name="imap",
    )

    indexer_lambda_stack.IndexerLambda(
        scope=scope,
        construct_id="IndexerLambda",
        env=env,
        db_secret_name=db_secret_name,
        vpc=networking.vpc,
        vpc_subnets=rds_stack.rds_subnet_selection,
        rds_security_group=networking.rds_security_group,
        data_bucket=data_bucket.data_bucket,
        sns_topic=monitoring.sns_topic_notifications,
    )

    sds_api_manager_stack.SdsApiManager(
        scope=scope,
        construct_id="SdsApiManager",
        api=api,
        env=env,
        data_bucket=data_bucket.data_bucket,
        vpc=networking.vpc,
        rds_security_group=networking.rds_security_group,
        db_secret_name=db_secret_name,
    )

    # create EFS
    efs_instance = efs_stack.EFSStack(scope, "EFSStack", networking.vpc, env=env)

    lambda_code_directory = Path(__file__).parent.parent / "lambda_code"
    lambda_code_directory_str = str(lambda_code_directory.resolve())

    # This valid instrument list is from imap-data-access package
    for instrument in imap_data_access.VALID_INSTRUMENTS:
        ecr = ecr_stack.EcrStack(
            scope,
            f"{instrument}Ecr",
            env=env,
            instrument_name=f"{instrument}",
        )

        batch_compute_resources.FargateBatchResources(
            scope,
            construct_id=f"{instrument}BatchJob",
            vpc=networking.vpc,
            processing_step_name=instrument,
            data_bucket=data_bucket.data_bucket,
            repo=ecr.container_repo,
            db_secret_name=db_secret_name,
            efs_instance=efs_instance,
            account_name=account_name,
            env=env,
        )

    instrument_lambdas.BatchStarterLambda(
        scope,
        "BatchStarterLambda",
        data_bucket=data_bucket.data_bucket,
        code_path=lambda_code_directory_str,
        rds_stack=rds_stack,
        rds_security_group=networking.rds_security_group,
        subnets=rds_stack.rds_subnet_selection,
        vpc=networking.vpc,
        env=env,
    )

    create_schema_stack.CreateSchema(
        scope,
        "CreateSchemaStack",
        env=env,
        db_secret_name=db_secret_name,
        vpc=networking.vpc,
        vpc_subnets=rds_stack.rds_subnet_selection,
        rds_security_group=networking.rds_security_group,
    )

    # create lambda that mounts EFS and writes data to EFS
    efs_stack.EFSWriteLambda(
        scope=scope,
        construct_id="EFSWriteLambda",
        vpc=networking.vpc,
        data_bucket=data_bucket.data_bucket,
        efs_instance=efs_instance,
        env=env,
    )

    # I-ALiRT IOIS ECR
    ialirt_ecr = ecr_stack.EcrStack(
        scope,
        "IalirtEcr",
        env=env,
        instrument_name="IalirtEcr",
    )

    # All traffic to I-ALiRT is directed to listed container ports
    ialirt_ports = {"Primary": [8080, 8081], "Secondary": [80]}
    container_ports = {"Primary": 8080, "Secondary": 80}

    for primary_or_secondary in ialirt_ports:
        ialirt_processing_stack.IalirtProcessing(
            scope,
            f"IalirtProcessing{primary_or_secondary}",
            env=env,
            vpc=networking.vpc,
            repo=ialirt_ecr.container_repo,
            processing_name=primary_or_secondary,
            ialirt_ports=ialirt_ports[primary_or_secondary],
            container_port=container_ports[primary_or_secondary],
        )


def build_backup(scope: App, env: Environment, source_account: str):
    """Build backup bucket with permissions for replication from source_account.

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
