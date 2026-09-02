"""Fixtures and safety guards for the integration test suite.

This suite is destructive and runs against a live, deployed environment. To
avoid accidents:

* It refuses to run against the production account.
* Every test here is auto-marked ``integration`` and ``network`` so a normal
  ``poetry run pytest`` (and CI, which runs ``-m "not network"``) skips them.

Run it explicitly with::

    poetry run pytest -s -m integration tests/integration
"""

import boto3
import pytest
from tests.integration.integration_test_files import SOURCE_FILES
from tests.integration import helpers

# Production account: these tests must NEVER touch it.
PROD_ACCOUNT = "593025701104"

# Region the dev environment is deployed to.
REGION = "us-west-2"

# Name of the Secrets Manager secret holding the main SDS RDS credentials.
DB_SECRET_NAME = "sdp-database-cred"  # noqa: S105 - secret name, not a secret

def pytest_collection_modifyitems(config, items):
    """Auto-mark everything in this package as integration + network."""
    for item in items:
        if "tests/integration" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.integration)
            item.add_marker(pytest.mark.network)


@pytest.fixture(scope="session")
def boto_session():
    """Return a boto3 session pinned to the dev region."""
    return boto3.session.Session(region_name=REGION)


@pytest.fixture(scope="session", autouse=True)
def guard_account(boto_session):
    """Abort the entire session if pointed at the production account."""
    account = boto_session.client("sts").get_caller_identity()["Account"]
    if account == PROD_ACCOUNT:
        pytest.exit(
            f"Refusing to run integration tests against production account {account}.",
            returncode=2,
        )
    return account


@pytest.fixture(scope="session")
def data_bucket(guard_account):
    """Return the dev data bucket name, derived from the caller's account id."""
    return f"sds-data-{guard_account}"


@pytest.fixture(scope="session")
def sds_db_engine(boto_session, guard_account):
    """SQLAlchemy engine for the main SDS RDS.

    The RDS security group only allows a fixed LASP CIDR, so we temporarily add
    the runner's public IP, hand back an engine, and revoke the rule on
    teardown.
    """
    secrets_client = boto_session.client("secretsmanager")
    ec2_client = boto_session.client("ec2")
    rds_client = boto_session.client("rds")

    db_config = helpers.get_db_config(secrets_client, DB_SECRET_NAME)
    group_id = helpers.get_rds_security_group_id(rds_client, db_config["host"])
    cidr = f"{helpers.get_public_ip()}/32"

    helpers.add_ingress_rule(ec2_client, group_id, cidr)
    engine = helpers.build_engine(db_config)
    try:
        yield engine
    finally:
        print("Removing ingress rule and disposing engine...")
        engine.dispose()
        helpers.revoke_ingress_rule(ec2_client, group_id, cidr)


@pytest.fixture(scope="session", autouse=True)
def _environment_setup(boto_session, guard_account, sds_db_engine, data_bucket):
    """Reset the environment, then seed it with the source files.

    Ordering matters: everything is cleaned first, then input files are copied
    in so the pipeline reacts to a known, fresh set of inputs.
    """
    s3_client = boto_session.client("s3")
    ecs_client = boto_session.client("ecs")

    print("Clearing out database tables...")
    helpers.wipe_all_tables(sds_db_engine)
    print("Wiping all Dagster assets...")
    helpers.run_dagster_asset_wipe(ecs_client)
    print("Resetting Dagster kickoff sensor cursors...")
    helpers.run_dagster_sensor_cursor_reset(ecs_client)
    print("Wiping data bucket...")
    helpers.wipe_data_bucket(s3_client, data_bucket)
    print("Copying source files to data bucket...")
    helpers.copy_source_files(s3_client, data_bucket, SOURCE_FILES)
