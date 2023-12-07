from aws_cdk import (
    CfnOutput,
    Duration,
    Fn,
    Stack,
    aws_iam,
    aws_lambda,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_efs as efs,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct


class EFSStack(Stack):
    """
    Elastic File System for storing various software and data.

    This file system can be mounted by multiple resources. It
    will be attached to AWS resources that run the
    models (ECS, EC2, Lambda) and produce output data.  It will
    also be mounted by the visualizer app to view that model data.
    This allows model data to be seamlessly transferred between
    resources without having to push/pull from s3. For the
    visualizer display, it should have decent I/O capabilities
    to read and write the model output data to the frontend quickly.

    NOTE: The EFS file systems will not be removed during a CDK
    destroy. This is a protection mechanism to
    prevent data from being deleted. After destroying this
    stack you will need to manually delete these
    resources via the AWS console or CLI.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Initialize EFS related information that other resources
        # will need to access EFS or mount EFS.
        self.volume_name = "EFS"
        self.efs_path = "/data"
        self.efs_spice_path = "/data/spice"
        self.spice_access_point_id_name = "spice-access-point-id"
        self.efs_fs_id_name = "efs-filesystem-id"

        # Define EFS security group, ports are added in EC2 stack
        self.efs_security_group = ec2.SecurityGroup(
            self,
            "EFSSecurityGroup",
            vpc=vpc,
            description="No outbound rule for EFS",
            allow_all_outbound=False,
            security_group_name="EFSSecurityGroup",
        )

        # Add inbound rule for TCP port 2049
        self.efs_security_group.connections.allow_from_any_ipv4(
            ec2.Port.tcp(2049),
            "Allow services to mount the EFS",
        )

        self.efs = efs.FileSystem(
            self,
            "EFS",
            vpc=vpc,
            file_system_name=self.volume_name,
            # This will automatically downscale infrequently accessed
            # data to a cheaper storage tier after 14 days of inactivity.
            # TODO: Investigate what lifecycle policy we need or if
            # we should remove if it effects read latency.
            lifecycle_policy=efs.LifecyclePolicy.AFTER_14_DAYS,
            # TODO: Investigate what performance characteristics we need
            # For now, use general purpose, but we can experiment later.
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            security_group=self.efs_security_group,
            # Need to add EFS mount points to all Private subnets
            # This will allow EC2 in either AZ to connect without data
            # transfer charges
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
        )

        # Make an access point for others to be able to access the drive
        self.spice_access_point = self.efs.add_access_point(
            "SpiceAccessPoint",
            create_acl=efs.Acl(owner_gid="1000", owner_uid="1000", permissions="750"),
            path=self.efs_spice_path,
            posix_user=efs.PosixUser(gid="1000", uid="1000"),
        )

        # Export the access point value to avoid a circular dependency between
        # the EFS stack and API/Lambda stack
        # https://github.com/aws/aws-cdk/issues/18759
        CfnOutput(
            self,
            "efs-access-pt-id",
            export_name=self.spice_access_point_id_name,
            value=self.spice_access_point.access_point_id,
        )

        CfnOutput(
            self,
            "efs-fs-id",
            export_name=self.efs_fs_id_name,
            value=self.efs.file_system_id,
        )


class EFSWriteLambda(Stack):
    """
    Creates some Lambdas that write to the EFS file system.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        data_bucket: s3.Bucket,
        efs_instance: efs.FileSystem,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create a role for the EFS Lambda
        # Grant the Lambda identity role access to the VPC/EFS
        iam_role_name = "efs-lambda-role"
        efs_lambda_role = aws_iam.Role(
            self,
            iam_role_name,
            role_name=iam_role_name,
            managed_policies=[
                aws_iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                aws_iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
                aws_iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonS3FullAccess"
                ),
                aws_iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonElasticFileSystemFullAccess"
                ),
            ],
            assumed_by=aws_iam.ServicePrincipal("lambda.amazonaws.com"),
        )

        # Create a security group that allows network access to mount the EFS
        self.efs_spice_ingest_sg = ec2.SecurityGroup(
            self,
            "LambdaEFSSecurityGroup",
            vpc=vpc,
            description="Allow calls to internet",
            allow_all_outbound=True,
            security_group_name="LambdaEFSSecurityGroup",
        )

        # NOTE: Workaround to overcome EFS circular dependency when mounting
        # a filesystem
        # https://github.com/aws/aws-cdk/issues/18759
        spice_access_point_id = Fn.import_value(efs_instance.spice_access_point_id_name)

        # This access point is used by other resources to read from EFS
        lambda_mount_path = "/mnt/spice"
        lambda_efs_access = aws_lambda.FileSystem(
            arn=f"arn:aws:elasticfilesystem:{self.region}:{self.account}:access-point/{spice_access_point_id}",
            local_mount_path=lambda_mount_path,
        )

        lambda_code = aws_lambda.Code.from_asset(
            "sds_data_manager/lambda_code/efs_lambda"
        )

        self.efs_spice_ingest_lambda = aws_lambda.Function(
            self,
            "EFSWriteLambda",
            function_name="efs-write-lambda",
            runtime=aws_lambda.Runtime.PYTHON_3_11,
            code=lambda_code,
            handler="lambda_function.lambda_handler",
            role=efs_lambda_role,
            description="Lambda that write data to the EFS",
            # Access to the EFS requires to be within the VPC
            vpc=vpc,
            # Mount EFS access point to /mnt/data within the lambda
            filesystem=lambda_efs_access,
            timeout=Duration.minutes(1),
            # Allow access to the EFS over NFS port
            security_groups=[self.efs_spice_ingest_sg],
            architecture=aws_lambda.Architecture.ARM_64,
            environment={
                "EFS_MOUNT_PATH": lambda_mount_path,
            },
        )

        # Trigger lambda on all s3 object creations through
        # eventbridge

        # Define an EventBridge rule
        event_rule = events.Rule(
            self,
            "EfsWriteLambdaS3EventRule",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [data_bucket.bucket_name]},
                    "object": {
                        "key": [
                            {"prefix": "imap/spice/imap"},
                            {"suffix": "ah.a"},
                            {"suffix": ".bsp"},
                        ]
                    },
                },
            ),
        )

        # Add the Lambda function as the target for the rule
        event_rule.add_target(targets.LambdaFunction(self.efs_spice_ingest_lambda))
