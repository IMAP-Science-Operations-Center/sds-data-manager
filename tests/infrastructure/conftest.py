import pytest
from aws_cdk import App


@pytest.fixture(scope="module")
def sds_id():
    return "sdsid-test"


@pytest.fixture(scope="module")
def app(sds_id):
    return App(context={"SDSID": sds_id})
