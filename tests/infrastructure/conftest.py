import pytest
from aws_cdk import App, Environment


@pytest.fixture(scope="module")
def account():
    return "1234567890"


@pytest.fixture(scope="module")
def region():
    return "us-east-1"


@pytest.fixture(scope="module")
def sds_id():
    return "sdsid-test"


@pytest.fixture(scope="module")
def env(account, region):
    return Environment(account=account, region=region)


@pytest.fixture(scope="module")
def app(sds_id):
    return App(context={"SDSID": sds_id})
