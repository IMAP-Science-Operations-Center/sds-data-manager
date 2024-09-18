"""Test the monitoring."""

import pytest
from aws_cdk.assertions import Template

from sds_data_manager.constructs.monitoring_construct import MonitoringConstruct


@pytest.fixture()
def template(stack):
    """Return a template monitoring stack."""
    MonitoringConstruct(
        stack,
        construct_id="MonitorTest",
    )

    template = Template.from_stack(stack)
    return template


def test_monitoring(template):
    """Ensure the template has appropriate SNS."""
    template.resource_count_is("AWS::SNS::Topic", 1)
    template.has_resource_properties(
        "AWS::SNS::Topic",
        props={
            "DisplayName": "sns-notifications",
        },
    )
