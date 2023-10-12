"""Module with helper functions for creating standard sets of stacks"""
from pathlib import Path

from aws_cdk import App, Environment
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds

from sds_data_manager.stacks import (
    api_gateway_stack,
    backup_bucket_stack,
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
        sds_id: str,
        rds_size: str = 'SMALL',
        rds_class: str = 'BURSTABLE3',
        rds_storage: int = 200,
        use_custom_domain: bool = False
):
    """Builds the entire SDS

    Parameters
    ----------
    scope : App
    env : Environment
        Account and region
    sds_id : str
        Name suffix for stack
    rds_size : str, Optional
        Size of RDS
    rds_class : str, Optional
        Type of RDS
    rds_storage : int, Optional
        Max allowable storage in GiB
    use_custom_domain : bool, Optional
        Build API Gateway using custom domain
    """
    open_search = opensearch_stack.OpenSearch(
        scope, f"OpenSearch-{sds_id}", sds_id, env=env
    )

    dynamodb = dynamodb_stack.DynamoDB(
        scope,
        construct_id=f"DynamoDB-{sds_id}",
        sds_id=sds_id,
        table_name=f"imap-data-watcher-{sds_id}",
        partition_key="instrument",
        sort_key="filename",
        env=env,
    )

    # TODO: discuss taking components of this to conform to
    # other step function processing steps
    processing_step_function = step_function_stack.ProcessingStepFunctionStack(
        scope,
        f"ProcessingStepFunctionStack-{sds_id}",
        sds_id,
        dynamodb_table_name=dynamodb.table_name,
        env=env,
    )

    data_manager = sds_data_manager_stack.SdsDataManager(
        scope,
        f"SdsDataManager-{sds_id}",
        sds_id,
        open_search,
        dynamodb,
        processing_step_function_arn=processing_step_function.sfn.state_machine_arn,
        env=env,
    )

    domain = domain_stack.Domain(
        scope,
        f"DomainStack-{sds_id}",
        sds_id,
        env=env,
        use_custom_domain=use_custom_domain,
    )

    api_gateway_stack.ApiGateway(
        scope,
        f"ApiGateway-{sds_id}",
        sds_id,
        data_manager.lambda_functions,
        env=env,
        hosted_zone=domain.hosted_zone,
        certificate=domain.certificate,
        use_custom_domain=use_custom_domain,
    )

    networking = networking_stack.NetworkingStack(
        scope, f"Networking-{sds_id}", sds_id, env=env
    )

    rds_stack = database_stack.SdpDatabase(scope, "RDS",
                                     description="IMAP SDP database.",
                                     env=env,
                                     vpc=networking.vpc,
                                     rds_security_group=networking.rds_security_group,
                                     engine_version=rds.PostgresEngineVersion.VER_14_2,
                                     instance_size=ec2.InstanceSize[rds_size],
                                     instance_class=ec2.InstanceClass[rds_class],
                                     max_allocated_storage=rds_storage,
                                     username="postgres",
                                     secret_name="sdp-database-creds",
                                     database_name="imapdb")

    instrument_list = ["Codice"]  # etc

    lambda_code_directory = Path(__file__).parent / ".." / "lambda_code" / "SDSCode"
    lambda_code_directory_str = str(lambda_code_directory.resolve())

    for instrument in instrument_list:
        ecr = ecr_stack.EcrStack(
            scope,
            f"{instrument}Processing-{sds_id}",
            env=env,
            instrument_name=f"{instrument}-{sds_id}",
        )

        processing_stack.ProcessingStep(
            scope,
            f"L1b{instrument}Processing-{sds_id}",
            sds_id,
            env=env,
            vpc=networking.vpc,
            processing_step_name=f"l1b-{instrument}-{sds_id}",
            lambda_code_directory=lambda_code_directory_str,
            data_bucket=data_manager.data_bucket,
            instrument_target=f"l1b_{instrument}",
            instrument_sources=f"l1a_{instrument}",
            repo=ecr.container_repo,
            batch_security_group=networking.batch_security_group,
            rds_security_group=networking.rds_security_group,
            subnets=rds_stack.rds_subnet_selection,
            db_secret_name=rds_stack.secret_name,
        )

        processing_stack.ProcessingStep(
            scope,
            f"L1c{instrument}Processing-{sds_id}",
            sds_id,
            env=env,
            vpc=networking.vpc,
            processing_step_name=f"l1c-{instrument}-{sds_id}",
            lambda_code_directory=lambda_code_directory_str,
            data_bucket=data_manager.data_bucket,
            instrument_target=f"l1c_{instrument}",
            instrument_sources=f"l1b_{instrument}",
            repo=ecr.container_repo,
            batch_security_group=networking.batch_security_group,
            rds_security_group=networking.rds_security_group,
            subnets=rds_stack.rds_subnet_selection,
            db_secret_name=rds_stack.secret_name,
        )
        # etc


def build_backup(scope: App, env: Environment, sds_id: str, source_account: str):
    """Builds backup bucket with permissions for replication from source_account.

    Parameters
    ----------
    scope : App
    env : Environment
        Account and region
    sds_id : str
        Name suffix for stack
    source_account : str
        Account number for source bucket for replication
    """
    # This is the S3 bucket used by upload_api_lambda
    backup_bucket_stack.BackupBucket(
        scope, f"BackupBucket-{sds_id}", sds_id, env=env, source_account=source_account
    )
