import pytest
from aws_cdk import App, Environment


@pytest.fixture(scope="module")
def account():
    return "1234567890"


@pytest.fixture(scope="module")
def region():
    return "us-east-1"


@pytest.fixture(scope="module")
def env(account, region):
    return Environment(account=account, region=region)


@pytest.fixture(scope="module")
def app():
    return App()
