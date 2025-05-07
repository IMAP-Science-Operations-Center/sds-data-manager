"""Configure the EFS."""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_efs as efs
from constructs import Construct


class EFSConstruct(Construct):
    """Elastic File System for storing various software and data.

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
        """Construct the EFS.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        vpc : ec2.Vpc
            VPC into which to put the resources that require networking.
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        # Initialize EFS related information that other resources
        # will need to access EFS or mount EFS.
        self.volume_name = "SPICE-EFS"
        self.efs_path = "/data"

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
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # Make an access point for others to be able to access the drive
        self.spice_access_point = self.efs.add_access_point(
            "SpiceAccessPoint",
            create_acl=efs.Acl(owner_gid="1000", owner_uid="1000", permissions="750"),
            path=self.efs_path,
            posix_user=efs.PosixUser(gid="1000", uid="1000"),
        )
