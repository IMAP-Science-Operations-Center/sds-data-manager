"""Test the monitoring stack."""

import pytest
from aws_cdk.assertions import Template

from sds_data_manager.stacks.monitoring_stack import MonitoringStack


@pytest.fixture(scope="module")
def template(app):
    """Return a template monitoring stack."""
    monitor = MonitoringStack(
        app,
        construct_id="MonitorTest",
    )

    template = Template.from_stack(monitor)
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
