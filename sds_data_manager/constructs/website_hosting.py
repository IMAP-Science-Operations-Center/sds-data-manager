"""All resources to  put together a public facing website."""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
)
from aws_cdk import (
    aws_cloudfront as cloudfront,
)
from aws_cdk import (
    aws_cloudfront_origins as origins,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_route53 as route53,
)
from aws_cdk import (
    aws_route53_targets as targets,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct

from sds_data_manager.constructs.route53_hosted_zone import DomainConstruct


class Website(Construct):
    """Website hosting resources.

    It takes ~5 minutes to redeploy this stack for any cloudfront updates.

    In order to provide https access to the S3 content, Cloudfront is
    required to provide the SSL termination from the browser request.

    This should be deployed in us-east-1 for the CloudFront SSL certs.

    Resources:
    - S3 Bucket for website hosting
    - CloudFront distribution to serve the S3 content
    - Route53 DNS record to point to the CloudFront distribution
    - IAM group and policy for automated frontend deployments
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        domain: DomainConstruct,
        **kwargs,
    ) -> None:
        """Create the website hosting stack.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        domain : DomainConstruct
            The domain construct containing hosted zone and certificate.
        kwargs : dict
            Extra keyword arguments
        """
        super().__init__(scope, construct_id, **kwargs)

        # Create an s3 bucket for our website hosting
        s3_bucket = s3.Bucket(
            self,
            "website-bucket",
            bucket_name=f"imap-website-{domain.domain_name}",
            public_read_access=False,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # Create the Cloudfront Function used to change the uri
        code_path = str(
            Path(__file__).parent.parent / "cloudfront_functions/update_uri_routes.js"
        )
        cf_function = cloudfront.Function(
            self,
            "website-CF-Function",
            code=cloudfront.FunctionCode.from_file(file_path=code_path),
            function_name="WebsiteURIRouteRequestUpdate",
        )

        # Setup CF dist to serve the data over https
        self.distribution = cloudfront.Distribution(
            self,
            "cloudfront_distribution",
            default_behavior=cloudfront.BehaviorOptions(
                # origin_path restricts CloudFront access to frontend/ S3 data
                origin=origins.S3Origin(s3_bucket, origin_path="frontend/"),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                # We want to disable caching so updates to the S3 bucket are reflected
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                # Function to help update URI to Angular/SPA routes
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=cf_function,
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    )
                ],
            ),
            domain_names=[domain.domain_name],
            # Lowest price limits to US/CANADA/EU
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            certificate=domain.certificate,
            default_root_object="/live/index.html",
            comment="CF dist to serve IMAP S3 frontend content",
            error_responses=[
                # Web Team frontend apps uses SPA, so redirects are required
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/live/index.html",
                    ttl=Duration.seconds(10),
                ),
            ],
        )

        # DNS record for CF -> S3 bucket access
        # May need to wait 5-10 minutes after this record is created
        # to propogate through all domain servers
        self.route53_domain_name = route53.ARecord(
            self,
            "CFAliasRecord",
            zone=domain.hosted_zone,
            # Route53 records automatically/always append the subdomain to new records.
            # We explicitiy pass in the S3 prefix, as extracting the prefix from the
            # bucket name within the CFT is not as simple as a python replace() method
            # record_name=s3_bucket_name_prefix,
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(self.distribution)
            ),
        )

        frontend_group = iam.Group(self, "FrontendGroup", group_name="FrontendGroup")

        frontend_iam_policy = iam.Policy(
            self,
            "FrontendCICDPolicy",
            policy_name="frontend-automated-deploy",
            statements=[
                iam.PolicyStatement(
                    # Permission to list all S3 buckets
                    effect=iam.Effect.ALLOW,
                    actions=["s3:GetBucketLocation", "s3:ListAllMyBuckets"],
                    resources=[
                        "arn:aws:s3:::*",
                    ],
                ),
                iam.PolicyStatement(
                    # Permission to do anything to objects inside this specific bucket
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "s3:*",
                    ],
                    resources=[
                        f"{s3_bucket.bucket_arn}",
                        f"{s3_bucket.bucket_arn}/*",
                    ],
                ),
            ],
        )
        frontend_iam_policy.attach_to_group(frontend_group)
        # Optionally, create a user and add the user to the group
        frontend_user = iam.User(
            self, "FrontendAutomatedUser", user_name="FrontendAutomatedUser"
        )
        frontend_group.add_user(frontend_user)
