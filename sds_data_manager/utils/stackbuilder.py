"""Module with helper functions for creating standard sets of stacks."""

from pathlib import Path

import imap_data_access
from aws_cdk import App, Environment, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_rds as rds

from sds_data_manager.constructs import (
    api_gateway_construct,
    backup_bucket_construct,
    dagster_construct,
    data_bucket_construct,
    database_construct,
    ialirt_alarm_construct,
    ialirt_api_manager_construct,
    ialirt_archive_construct,
    ialirt_bucket_construct,
    ialirt_coverage_construct,
    ialirt_efs_construct,
    ialirt_ingest_lambda_construct,
    ialirt_pointing_schedule_construct,
    ialirt_processing_construct,
    ialirt_realtime_construct,
    ialirt_schedule_fetch_construct,
    indexer_lambda_construct,
    instrument_lambdas,
    lambda_layer_construct,
    monitoring_construct,
    monitoring_lambda_construct,
    networking_construct,
    packet_downloader_lambda_construct,
    processing_construct,
    route53_hosted_zone,
    sds_api_manager_construct,
    spice_monitoring_construct,
    website_hosting,
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
    networking_stack = Stack(scope, "NetworkingStack", env=env)
    networking = networking_construct.NetworkingConstruct(
        networking_stack, "Networking"
    )

    domain = None
    domain_name = account_config.get("domain_name", None)
    us_east_env = Environment(account=env.account, region="us-east-1")
    hosted_zone_stack = Stack(scope, "HostedZoneCertificateStack", env=us_east_env)
    account_name = account_config["account_name"]
    if account_name == "prod":
        # This is for the root level account So it should be the base url
        # e.g."imap-mission.com"
        domain = route53_hosted_zone.DomainConstruct(
            hosted_zone_stack,
            "HostedZoneConstruct",
            domain_name,
            create_new_hosted_zone=True,
        )
        domain.setup_cf_and_lambda_authorizer(allowed_ip="128.138.131.13")  # LASP IPs
    elif domain_name is not None:
        # This is for the subaccounts, so it should be the subdomain url
        # e.g. "dev.imap-mission.com"
        domain = route53_hosted_zone.DomainConstruct(
            hosted_zone_stack,
            "HostedZoneConstruct",
            domain_name,
            create_new_hosted_zone=True,
        )

    # Make the website stack only if we have a domain name
    # This needs to be deployed in us-east-1 for the CloudFront SSL certs
    if domain is not None:
        website_stack = Stack(scope, "WebsiteStack", env=us_east_env)
        website_hosting.Website(website_stack, "WebsiteConstruct", domain=domain)

    sdc_stack = Stack(scope, "SDCStack", cross_region_references=True, env=env)

    root_certificate = None
    if domain is not None:
        root_certificate = acm.Certificate(
            sdc_stack,
            "DomainRegionCertificate",
            domain_name=f"*.{domain_name}",  # *.imap-mission.com
            subject_alternative_names=[domain_name],  # imap-mission.com
            validation=acm.CertificateValidation.from_dns(
                hosted_zone=domain.hosted_zone
            ),
        )

    # Adding this endpoint so that lambda within
    # this VPC can perform boto3.client("events")
    # or boto3.client("batch") operations
    networking.vpc.add_interface_endpoint(
        "EventBridgeEndpoint",
        service=ec2.InterfaceVpcEndpointAwsService.EVENTBRIDGE,
    )
    networking.vpc.add_interface_endpoint(
        "BatchJobEndpoint", service=ec2.InterfaceVpcEndpointAwsService.BATCH
    )

    # The lambda is in the same private security group as the RDS, but
    # it needs to access the secrets manager, so we add this endpoint.
    networking.vpc.add_interface_endpoint(
        "SecretManagerEndpoint",
        service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
        subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        private_dns_enabled=True,
    )

    data_bucket = data_bucket_construct.DataBucketConstruct(
        scope=sdc_stack, construct_id="DataBucket", env=env
    )

    monitoring = monitoring_construct.MonitoringConstruct(
        scope=sdc_stack,
        construct_id="MonitoringConstruct",
    )

    api = api_gateway_construct.ApiGateway(
        scope=sdc_stack,
        construct_id="ApiGateway",
        domain_construct=domain,
        certificate=root_certificate,
        create_api_keys_table=True,  # Create the API keys table in SDC stack
    )
    api.deliver_to_sns(monitoring.sns_topic_notifications)

    # create Code asset and Layer for Lambda(s)
    layer_code_directory = (
        Path(__file__).parent.parent.parent / "lambda_layer"
    ).resolve()
    lambda_code_directory = Path(__file__).parent.parent / "lambda_code"

    lambda_code = lambda_.Code.from_asset(str(lambda_code_directory))
    db_lambda_layer = lambda_layer_construct.IMAPLambdaLayer(
        scope=sdc_stack,
        id="DatabaseDependencies",
        layer_dependencies_dir=str(layer_code_directory / "database"),
    )
    spice_lambda_layer = lambda_layer_construct.IMAPLambdaLayer(
        scope=sdc_stack,
        id="PythonDependencies",
        layer_dependencies_dir=str(layer_code_directory / "spice"),
    )

    # Get RDS properties from account_config
    rds_size = account_config.get("rds_size", "SMALL")
    rds_class = account_config.get("rds_class", "BURSTABLE3")
    rds_storage = account_config.get("rds_construct", 200)
    db_secret_name = "sdp-database-cred"  # noqa
    # Create an RDS instance and a Lambda function to automatically create the schema
    rds_construct = database_construct.SdpDatabase(
        scope=sdc_stack,
        construct_id="RDS",
        vpc=networking.vpc,
        engine_version=rds.PostgresEngineVersion.VER_15_7,
        instance_size=ec2.InstanceSize[rds_size],
        instance_class=ec2.InstanceClass[rds_class],
        max_allocated_storage=rds_storage,
        username="imap_user",
        secret_name=db_secret_name,
        database_name="imap",
        code=lambda_code,
        layers=[db_lambda_layer, spice_lambda_layer],
    )
    rds_construct.add_synchronizer(
        code=lambda_code,
        layers=[db_lambda_layer, spice_lambda_layer],
        data_bucket=data_bucket.data_bucket,
        vpc=networking.vpc,
    )

    indexer_lambda_construct.IndexerLambda(
        scope=sdc_stack,
        construct_id="IndexerLambda",
        code=lambda_code,
        db_secret_name=db_secret_name,
        vpc=networking.vpc,
        vpc_subnets=rds_construct.rds_subnet_selection,
        rds_security_group=rds_construct.rds_security_group,
        data_bucket=data_bucket.data_bucket,
        layers=[db_lambda_layer, spice_lambda_layer],
    )

    indexer_lambda_construct.IDEXL0IndexerLambda(
        scope=sdc_stack,
        construct_id="IDEXL0IndexerLambda",
        db_secret_name=db_secret_name,
        vpc=networking.vpc,
        vpc_subnets=rds_construct.rds_subnet_selection,
        rds_security_group=rds_construct.rds_security_group,
        data_bucket=data_bucket.data_bucket,
    )

    monitoring_lambda_construct.MonitoringLambda(
        scope=sdc_stack,
        construct_id="MonitoringLambda",
        code=lambda_code,
        sns_topic=monitoring.sns_topic_notifications,
    )

    # Set SPICE monitoring email based on environment
    spice_alarm_email = (
        "imap-sdc@lists.lasp.colorado.edu" if account_name == "prod" else ""
    )
    spice_monitoring_construct.SpiceMonitoringConstruct(
        scope=sdc_stack,
        construct_id="SpiceMonitoringConstruct",
        code=lambda_code,
        db_secret_name=db_secret_name,
        vpc=networking.vpc,
        rds_security_group=rds_construct.rds_security_group,
        layers=[db_lambda_layer, spice_lambda_layer],
        alarm_email=spice_alarm_email,
        ck_threshold_days=4,
        spin_threshold_days=4,
        sclk_threshold_days=4,
        repoint_threshold_days=4,
        predicted_ephemeris_threshold_days=4,
    )

    sds_api_manager_construct.SdsApiManager(
        scope=sdc_stack,
        construct_id="SdsApiManager",
        code=lambda_code,
        api=api,
        env=env,
        data_bucket=data_bucket.data_bucket,
        vpc=networking.vpc,
        rds_security_group=rds_construct.rds_security_group,
        db_secret_name=db_secret_name,
        layers=[db_lambda_layer, spice_lambda_layer],
        account_name=account_name,
    )

    account_name = sdc_stack.node.get_context("account_name")
    # once we have the account_name, get that section out of cdk.json
    account_config = sdc_stack.node.get_context(account_name)
    domain_name = account_config.get("domain_name", "no-domain-set")
    # https://api.imap-mission.com
    # https://api.dev.imap-mission.com
    # Append the /api-key so that these jobs are able to be authenticated
    # to access the data and upload results as necessary.
    general_data_access_url = f"https://api.{domain_name}"
    api_key_data_access_url = f"{general_data_access_url}/api-key"

    # Packet Downloader Lambda
    packet_downloader_lambda_construct.PacketDownloaderLambda(
        scope=sdc_stack,
        construct_id="PacketDownloaderLambda",
        code=lambda_code,
        data_bucket=data_bucket.data_bucket,
        vpc=networking.vpc,
        layers=[db_lambda_layer],
        data_access_url=api_key_data_access_url,
    )

    # This valid instrument list is from imap-data-access package
    processing = processing_construct.ProcessingConstruct(
        sdc_stack, "ProcessingConstruct", vpc=networking.vpc
    )
    for instrument in imap_data_access.VALID_INSTRUMENTS:
        for step in ["", "-l3"]:
            # "swe" or "swe-l3"
            processing.add_job(
                f"{instrument.lower()}{step}", data_access_url=api_key_data_access_url
            )

    # Create lambda that mounts EFS and writes SPICE files to the EFS and the database
    indexer_lambda_construct.SPICEIndexerLambda(
        scope=sdc_stack,
        construct_id="SPICEIndexerLambda",
        code=lambda_code,
        db_secret_name=db_secret_name,
        env=env,
        vpc=networking.vpc,
        layers=[db_lambda_layer, spice_lambda_layer],
        rds_security_group=rds_construct.rds_security_group,
        data_bucket=data_bucket.data_bucket,
    )

    # I-ALiRT Stack
    ialirt_stack = Stack(scope, "IalirtStack", cross_region_references=True, env=env)

    ialirt_spice_lambda_layer = lambda_layer_construct.IMAPLambdaLayer(
        scope=ialirt_stack,
        id="IAlirtSpiceDependencies",
        layer_dependencies_dir=str(layer_code_directory / "spice"),
    )
    ialirt_db_lambda_layer = lambda_layer_construct.IMAPLambdaLayer(
        scope=ialirt_stack,
        id="IAlirtDatabaseDependencies",
        layer_dependencies_dir=str(layer_code_directory / "database"),
    )

    ialirt_root_certificate = None
    if domain is not None:
        ialirt_root_certificate = acm.Certificate(
            ialirt_stack,
            "IAlirtDomainRegionCertificate",
            domain_name=f"*.{domain_name}",  # *.imap-mission.com
            subject_alternative_names=[domain_name],  # imap-mission.com
            validation=acm.CertificateValidation.from_dns(
                hosted_zone=domain.hosted_zone
            ),
        )

    # I-ALiRT IOIS S3 bucket
    ialirt_bucket = ialirt_bucket_construct.IAlirtBucketConstruct(
        scope=ialirt_stack, construct_id="IAlirtBucket", env=env
    )

    # create EFS
    ialirt_efs_instance = ialirt_efs_construct.IAlirtEFSConstruct(
        scope=ialirt_stack, construct_id="IAlirtEFSConstruct", vpc=networking.vpc
    )

    # I-ALiRT IOIS ingest lambda (facilitates s3 to dynamodb)
    ingest = ialirt_ingest_lambda_construct.IalirtIngestLambda(
        scope=ialirt_stack,
        construct_id="IalirtIngestLambda",
        ialirt_bucket=ialirt_bucket.ialirt_bucket,
        vpc=networking.vpc,
        efs_access_point=ialirt_efs_instance.spice_access_point,
        data_access_url=general_data_access_url,
        account_name=account_name,
    )

    # I-ALiRT IOIS archive lambda (facilitates dynamodb to s3)
    ialirt_archive_construct.IalirtArchiveConstruct(
        scope=ialirt_stack,
        construct_id="IalirtArchive",
        ialirt_bucket=ialirt_bucket.ialirt_bucket,
        data_table=ingest.data_table,
    )

    # I-ALiRT IOIS pointing schedule lambda
    ialirt_pointing_schedule_construct.IalirtPointingConstruct(
        scope=ialirt_stack,
        construct_id="IalirtPointingConstruct",
        ialirt_bucket=ialirt_bucket.ialirt_bucket,
        data_access_url=general_data_access_url,
    )

    # I-ALiRT schedule fetch lambda (polls external HTTPS endpoint for contact schedule)
    ialirt_schedule_fetch_construct.IalirtScheduleFetchConstruct(
        scope=ialirt_stack,
        construct_id="IalirtScheduleFetch",
        ialirt_bucket=ialirt_bucket.ialirt_bucket,
    )

    # I-ALiRT IOIS coverage lambda (facilitates creating coverage json in s3)
    ialirt_coverage_construct.IalirtCoverageConstruct(
        scope=ialirt_stack,
        construct_id="IalirtCoverage",
        ialirt_bucket=ialirt_bucket.ialirt_bucket,
        data_access_url=general_data_access_url,
    )

    # I-ALiRT IOIS realtime lambda (facilitates creating realtime json in s3)
    ialirt_realtime_construct.IalirtRealTimeConstruct(
        scope=ialirt_stack,
        construct_id="IalirtRealTime",
        ialirt_bucket=ialirt_bucket.ialirt_bucket,
    )

    ialirt_alarm_construct.IalirtAlarmConstruct(
        scope=ialirt_stack,
        construct_id="IalirtAlarm",
        code=lambda_.Code.from_asset(str(Path(__file__).parent.parent / "lambda_code")),
        ialirt_bucket=ialirt_bucket.ialirt_bucket,
        data_table=ingest.data_table,
    )

    ialirt_monitoring = monitoring_construct.MonitoringConstruct(
        scope=ialirt_stack,
        construct_id="IAlirtMonitoringConstruct",
    )

    ialirt_api = api_gateway_construct.ApiGateway(
        scope=ialirt_stack,
        construct_id="IAlirtApiGateway",
        domain_construct=domain,
        certificate=ialirt_root_certificate,
        ialirt_prefix="IAlirt",
        create_api_keys_table=False,  # Reference existing table from SDC stack
    )
    ialirt_api.deliver_to_sns(ialirt_monitoring.sns_topic_notifications)

    ialirt_api_manager_construct.IalirtApiManager(
        scope=ialirt_stack,
        construct_id="IAlirtApiManager",
        code=lambda_.Code.from_asset(str(Path(__file__).parent.parent / "lambda_code")),
        api=ialirt_api,
        env=env,
        data_bucket=ialirt_bucket.ialirt_bucket,
        vpc=networking.vpc,
        layers=[ialirt_spice_lambda_layer, ialirt_db_lambda_layer],
        algorithm_table=ingest.algorithm_data_table,
        data_table=ingest.data_table,
        account_name=account_name,
    )

    ialirt_secret_name = "nexus-credentials"  # noqa

    ialirt_processing_construct.IalirtProcessing(
        scope=ialirt_stack,
        construct_id="IalirtProcessing",
        env=env,
        vpc=networking.vpc,
        ialirt_bucket=ialirt_bucket.ialirt_bucket,
        secret_name=ialirt_secret_name,
        account_name=account_name,
    )

    reprocessing_tools_construct = instrument_lambdas.ReprocessingTools(
        scope=sdc_stack,
        construct_id="ReprocessingTools",
        api=api,
        code=lambda_code,
        rds_security_group=rds_construct.rds_security_group,
        vpc=networking.vpc,
        layers=[db_lambda_layer, spice_lambda_layer],
    )

    # Dagster Stack
    dagster_stack = Stack(scope, "DagsterStack", cross_region_references=True, env=env)

    dagster_repo_construct = dagster_construct.EcrConstruct(
        scope=dagster_stack,
        construct_id="DagsterImageECR",
        repo_name="dagsterimage",
    )

    # Repo root is three levels up from this
    # file (sds_data_manager/utils/stackbuilder.py)
    repo_root = str(Path(__file__).parent.parent.parent)
    dagster_image_construct = dagster_construct.DagsterDockerImageConstruct(
        scope=dagster_stack,
        construct_id="DagsterImageConstruct",
        image_name="DagsterImage",
        directory=repo_root,
        file="Dockerfile",
        ecr=dagster_repo_construct.container_repo.repository_uri,
    )

    dagster_database_construct = dagster_construct.DagsterDatabaseConstruct(
        scope=dagster_stack,
        construct_id="DagsterDatabase",
        vpc=networking.vpc,
        sg=rds_construct.rds_security_group,
    )

    logs_bucket = dagster_construct.DagsterS3LoggingBucket(
        scope=dagster_stack,
        construct_id="DagsterLogBucket",
    )

    dg_host_name = dagster_database_construct.db_instance.db_instance_endpoint_address
    dagster_env_vars = {
        "S3_BUCKET": f"sds-data-{env.account}",
        "SECRET_NAME": db_secret_name,
        "ACCOUNT": env.account,
        "REGION": env.region,
        "IMAP_DATA_ACCESS_URL": account_config.get(
            "imap_data_access_url", "https://api.dev.imap-mission.com"
        ),
        "SSM_API_KEY_PARAMETER": "/imap-sdc/batch-jobs/api-key",
        "REPROCESSING_SQS_URL": reprocessing_tools_construct.reprocessing_sqs_url,
        "DAGSTER_PG_HOST": dg_host_name,
        "DAGSTER_COMPUTE_LOG_BUCKET": logs_bucket.logs_bucket.bucket_name,
    }

    dagster_ecs_stack = dagster_construct.DagsterEcsConstruct(
        scope=dagster_stack,
        construct_id="DagsterEcsStack",
        vpc=networking.vpc,
        sg=rds_construct.rds_security_group,
        dagster_env_vars=dagster_env_vars,
        db_secret=dagster_database_construct.db_secret,
    )
    dagster_ecs_stack.node.add_dependency(dagster_image_construct)

    # NOTE: one time use lambda to transition s3 files to new version.
    instrument_lambdas.S3VersionTransitionLambda(
        scope=sdc_stack,
        construct_id="S3FileVersionTransitionLambda",
        env=env,
        data_bucket=data_bucket.data_bucket,
        code=lambda_code,
        rds_construct=rds_construct,
        rds_security_group=rds_construct.rds_security_group,
        vpc=networking.vpc,
        layers=[db_lambda_layer, spice_lambda_layer],
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
    backup_stack = Stack(scope, "BackupStack", env=env)
    # This is the S3 bucket used by upload_api_lambda
    backup_bucket_construct.BackupBucket(
        backup_stack,
        "BackupBucket",
        source_account=source_account,
    )
