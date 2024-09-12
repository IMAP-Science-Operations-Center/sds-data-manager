"""Test the website hosting resources."""

from aws_cdk.assertions import Template

from sds_data_manager.constructs.route53_hosted_zone import DomainConstruct
from sds_data_manager.constructs.website_hosting import Website


def test_website_resources(stack):
    """Test the website hosting resources."""
    domain = DomainConstruct(
        stack, "DomainConstruct", domain_name="test.com", create_new_hosted_zone=True
    )
    Website(
        stack,
        "WebsiteStack",
        domain=domain,
    )

    # Prepare the stack for assertions.
    template = Template.from_stack(stack)

    template.resource_count_is("AWS::CloudFront::Function", 1)

    template.resource_count_is("AWS::CloudFront::Distribution", 1)
    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        props={
            "DistributionConfig": {
                "CustomErrorResponses": [
                    {
                        "ErrorCachingMinTTL": 10,
                        "ErrorCode": 403,
                        "ResponseCode": 200,
                        "ResponsePagePath": "/live/index.html",
                    },
                ],
                "DefaultRootObject": "/live/index.html",
                "Enabled": True,
                "HttpVersion": "http2",
                "IPV6Enabled": True,
                "PriceClass": "PriceClass_100",
                "Origins": [
                    {
                        "OriginPath": "/frontend",
                    }
                ],
                # This Cache policy ID maps to caching disabled and is a static AWS ID
                # https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-cache-policies.html
                "DefaultCacheBehavior": {
                    "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
                },
            },
        },
    )

    # Make sure we have an A record set for this too
    template.resource_count_is("AWS::Route53::RecordSet", 1)
    template.has_resource_properties(
        "AWS::Route53::RecordSet",
        props={"Name": f"{domain.domain_name}.", "Type": "A"},
    )
