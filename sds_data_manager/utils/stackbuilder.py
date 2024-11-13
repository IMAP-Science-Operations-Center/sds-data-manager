"""Module with helper functions for creating standard sets of stacks."""

from pathlib import Path

import imap_data_access
from aws_cdk import App, Environment, Stack
from aws_cdk import aws_batch as batch
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_rds as rds

from sds_data_manager.constructs import (
    api_gateway_construct,
    backup_bucket_construct,
    data_bucket_construct,
    database_construct,
    efs_construct,
    ialirt_api_manager_construct,
    ialirt_bucket_construct,
    ialirt_ingest_lambda_construct,
    ialirt_processing_construct,
    indexer_lambda_construct,
    instrument_lambdas,
    lambda_layer_construct,
    monitoring_construct,
    networking_construct,
    processing_construct,
    route53_hosted_zone,
    sds_api_manager_construct,
    sqs_construct,
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
    if account_config["account_name"] == "prod":
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
    )
    api.deliver_to_sns(monitoring.sns_topic_notifications)

    # create Code asset and Layer for Lambda(s)
    layer_code_directory = (
        Path(__file__).parent.parent.parent / "lambda_layer/python"
    ).resolve()
    lambda_code_directory = Path(__file__).parent.parent / "lambda_code"

    lambda_code = lambda_.Code.from_asset(str(lambda_code_directory))
    db_lambda_layer = lambda_layer_construct.IMAPLambdaLayer(
        scope=sdc_stack,
        id="DatabaseDependencies",
        layer_dependencies_dir=str(layer_code_directory),
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
        engine_version=rds.PostgresEngineVersion.VER_15_6,
        instance_size=ec2.InstanceSize[rds_size],
        instance_class=ec2.InstanceClass[rds_class],
        max_allocated_storage=rds_storage,
        username="imap_user",
        secret_name=db_secret_name,
        database_name="imap",
        code=lambda_code,
        layers=[db_lambda_layer],
    )
    rds_construct.add_synchronizer(
        code=lambda_code,
        layers=[db_lambda_layer],
        bucket_name=data_bucket.data_bucket.bucket_name,
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
        sns_topic=monitoring.sns_topic_notifications,
        layers=[db_lambda_layer],
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
        layers=[db_lambda_layer],
    )

    # create EFS
    efs_instance = efs_construct.EFSConstruct(
        scope=sdc_stack, construct_id="EFSConstruct", vpc=networking.vpc
    )

    # This valid instrument list is from imap-data-access package
    processing_volumes = [
        batch.EfsVolume(
            name=f"{efs_instance.volume_name}-ECS-mount",
            access_point_id=efs_instance.spice_access_point.access_point_id,
            file_system=efs_instance.efs,
            container_path="/mnt/spice",
            enable_transit_encryption=True,
            transit_encryption_port=2049,
        )
    ]
    processing = processing_construct.ProcessingConstruct(
        sdc_stack, "ProcessingConstruct", vpc=networking.vpc, volumes=processing_volumes
    )
    for instrument in imap_data_access.VALID_INSTRUMENTS:
        for step in ["", "-l3"]:
            # "swe" or "swe-l3"
            processing.add_job(f"{instrument.lower()}{step}")

    # Create SQS pipeline for each instrument and add it to instrument_sqs
    instrument_sqs = sqs_construct.SqsConstruct(
        scope=sdc_stack,
        construct_id="SqsConstruct",
        instrument_names=imap_data_access.VALID_INSTRUMENTS,
    ).instrument_queue

    instrument_lambdas.BatchStarterLambda(
        scope=sdc_stack,
        construct_id="BatchStarterLambda",
        env=env,
        data_bucket=data_bucket.data_bucket,
        code=lambda_code,
        rds_construct=rds_construct,
        rds_security_group=rds_construct.rds_security_group,
        subnets=rds_construct.rds_subnet_selection,
        vpc=networking.vpc,
        sqs_queue=instrument_sqs,
        layers=[db_lambda_layer],
    )

    # Create lambda that mounts EFS and writes data to EFS
    efs_construct.EFSWriteLambda(
        scope=sdc_stack,
        construct_id="EFSWriteLambda",
        code=lambda_code,
        env=env,
        vpc=networking.vpc,
        data_bucket=data_bucket.data_bucket,
        efs_construct=efs_instance,
    )

    ialirt_stack = Stack(scope, "IalirtStack", cross_region_references=True, env=env)

    # I-ALiRT IOIS S3 bucket
    ialirt_bucket = ialirt_bucket_construct.IAlirtBucketConstruct(
        scope=ialirt_stack, construct_id="IAlirtBucket", env=env
    )

    ialirt_lambda_layer = lambda_layer_construct.IMAPLambdaLayer(
        scope=ialirt_stack,
        id="IAlirtDependencies",
        layer_dependencies_dir=str(layer_code_directory),
    )

    ialirt_monitoring = monitoring_construct.MonitoringConstruct(
        scope=ialirt_stack,
        construct_id="IAlirtMonitoringConstruct",
    )

    ialirt_api = api_gateway_construct.ApiGateway(
        scope=ialirt_stack,
        construct_id="IAlirtApiGateway",
        domain_construct=domain,
        certificate=root_certificate,
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
        layers=[ialirt_lambda_layer],
    )

    # All traffic to I-ALiRT is directed to listed container ports
    ports = {"Primary": [1234, 1235], "Secondary": [1236]}
    ialirt_secret_name = "nexus-credentials"  # noqa

    for primary_or_secondary in ports:
        ialirt_processing_construct.IalirtProcessing(
            scope=ialirt_stack,
            construct_id=f"IalirtProcessing{primary_or_secondary}",
            vpc=networking.vpc,
            processing_name=primary_or_secondary,
            ports=ports[primary_or_secondary],
            ialirt_bucket=ialirt_bucket.ialirt_bucket,
            secret_name=ialirt_secret_name,
        )

    # I-ALiRT IOIS ingest lambda (facilitates s3 to dynamodb)
    ialirt_ingest_lambda_construct.IalirtIngestLambda(
        scope=ialirt_stack,
        construct_id="IalirtIngestLambda",
        ialirt_bucket=ialirt_bucket.ialirt_bucket,
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
