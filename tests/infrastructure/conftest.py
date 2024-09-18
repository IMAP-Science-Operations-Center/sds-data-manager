"""Setup items for the infrastructure tests."""

import pytest
from aws_cdk import App, Environment, Stack, aws_lambda


@pytest.fixture()
def account():
    """Set the account number to test with."""
    return "1234567890"


@pytest.fixture()
def region():
    """Set the region to test with."""
    return "us-east-1"


@pytest.fixture()
def env(account, region):
    """Set the environment to test with."""
    return Environment(account=account, region=region)


@pytest.fixture()
def app():
    """Return the app to test with."""
    return App()


@pytest.fixture()
def stack(app, env):
    """Return the stack to test with."""
    return Stack(app, "TestStack", env=env)


@pytest.fixture()
def code():
    """Return a lambda code object."""
    return aws_lambda.Code.from_inline("def handler(event, context):\n    pass")
