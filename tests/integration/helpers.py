"""Helpers for the integration test suite.

These helpers talk to real AWS resources in a deployed (non-production)
environment. They are intentionally destructive: wiping databases, emptying
the data bucket, and clearing Dagster asset history so that each integration
run starts from a clean slate.
"""

import json
import time
import urllib.request

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from sds_data_manager.lambda_code.SDSCode.database.models import Base

# Port the RDS instances listen on.
POSTGRES_PORT = 5432

# Name of the container defined in the Dagster daemon task definition. Used to
# target the command override when launching the one-off asset-wipe task.
DAGSTER_DAEMON_CONTAINER = "DaemonContainer"

# Only sensors whose names end in this suffix have their cursors reset; other
# sensors (reprocessing, monitoring, etc.) are left alone.
KICKOFF_SENSOR_SUFFIX = "_kickoff_sensor"

# Run inside the Dagster daemon image via ``python -c``. It talks to the Dagster
# instance storage directly rather than shelling out to
# ``dagster sensor cursor --delete <name>`` once per sensor: that CLI loads the
# full workspace (all generated jobs) on every invocation, which would take
# minutes across the hundreds of kickoff sensors. Reading instigator state
# straight from the DB needs no workspace at all.
RESET_SENSOR_CURSORS_SCRIPT = """
from dagster import DagsterInstance
from dagster._core.definitions.run_request import InstigatorType

suffix = {suffix!r}
with DagsterInstance.get() as instance:
    states = instance.all_instigator_state(instigator_type=InstigatorType.SENSOR)
    reset = 0
    for state in states:
        data = state.instigator_data
        if not state.instigator_name.endswith(suffix) or data is None:
            continue
        if data.cursor is None:
            continue
        instance.update_instigator_state(state.with_data(data._replace(cursor=None)))
        print("Cleared cursor for", state.instigator_name)
        reset += 1
    print("Cleared cursors for", reset, "sensors")
"""


def get_public_ip() -> str:
    """Return the public IPv4 address of the machine running the tests.

    The main SDS RDS instance is publicly accessible but restricted by
    security-group ingress rules. GitHub Codespaces (and most CI runners) have
    no fixed IP, so we look ours up at runtime to temporarily whitelist it.
    """
    with urllib.request.urlopen(
        "https://checkip.amazonaws.com", timeout=15
    ) as response:
        return response.read().decode("utf-8").strip()


def get_db_config(secrets_client, secret_name: str) -> dict:
    """Fetch the RDS credentials JSON from Secrets Manager.

    The secret contains: ``username``, ``password``, ``host``, ``port``,
    ``dbname`` (mirrors the production lambda connection pattern).
    """
    secret_string = secrets_client.get_secret_value(SecretId=secret_name)[
        "SecretString"
    ]
    return json.loads(secret_string)


def build_engine(db_config: dict) -> Engine:
    """Build a SQLAlchemy engine from a Secrets Manager credentials dict."""
    db_uri = (
        f"postgresql://{db_config['username']}:{db_config['password']}"
        f"@{db_config['host']}:{db_config['port']}/{db_config['dbname']}"
    )
    return create_engine(db_uri)


def get_rds_security_group_id(rds_client, host: str) -> str:
    """Find the security-group id for the RDS instance with the given endpoint.

    The RDS instance's physical name changes on a full redeploy, so we match on
    the endpoint address (taken from the credentials secret) instead of a
    hard-coded identifier.
    """
    paginator = rds_client.get_paginator("describe_db_instances")
    for page in paginator.paginate():
        for instance in page["DBInstances"]:
            endpoint = instance.get("Endpoint", {})
            if endpoint.get("Address") == host:
                groups = instance.get("VpcSecurityGroups", [])
                if not groups:
                    raise RuntimeError(
                        f"RDS instance for host {host} has no security groups."
                    )
                return groups[0]["VpcSecurityGroupId"]
    raise RuntimeError(f"No RDS instance found with endpoint {host}.")


def add_ingress_rule(ec2_client, group_id: str, cidr: str) -> None:
    """Add a temporary Postgres ingress rule for ``cidr`` to ``group_id``.

    Idempotent: an already-present rule is treated as success.
    """
    try:
        ec2_client.authorize_security_group_ingress(
            GroupId=group_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": POSTGRES_PORT,
                    "ToPort": POSTGRES_PORT,
                    "IpRanges": [
                        {
                            "CidrIp": cidr,
                            "Description": "Temporary integration-test access",
                        }
                    ],
                }
            ],
        )
    except ec2_client.exceptions.ClientError as err:
        if "InvalidPermission.Duplicate" not in str(err):
            raise


def revoke_ingress_rule(ec2_client, group_id: str, cidr: str) -> None:
    """Remove the temporary Postgres ingress rule for ``cidr``.

    Idempotent: a missing rule is treated as success.
    """
    try:
        ec2_client.revoke_security_group_ingress(
            GroupId=group_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": POSTGRES_PORT,
                    "ToPort": POSTGRES_PORT,
                    "IpRanges": [{"CidrIp": cidr}],
                }
            ],
        )
    except ec2_client.exceptions.ClientError as err:
        if "InvalidPermission.NotFound" not in str(err):
            raise


def wipe_all_tables(engine: Engine) -> None:
    """Delete every row from every table in the SDS database schema.

    Dynamic-partition/spice/etc. tables are all defined on ``Base.metadata`` so
    a single ``TRUNCATE`` across the sorted tables clears them while resetting
    identity sequences. ``CASCADE`` handles any foreign-key relationships.
    """
    table_names = [table.name for table in Base.metadata.sorted_tables]
    if not table_names:
        return
    quoted = ", ".join(f'"{name}"' for name in table_names)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


def wipe_data_bucket(s3_client, bucket: str) -> int:
    """Delete every object under ``imap/`` from ``bucket``.

    Uses ``list_object_versions`` so the call works whether or not the bucket
    has versioning enabled. Returns the number of objects deleted.
    """
    paginator = s3_client.get_paginator("list_object_versions")
    deleted = 0
    for page in paginator.paginate(Bucket=bucket, Prefix="imap/"):
        objects = [
            {"Key": item["Key"], "VersionId": item["VersionId"]}
            for key in ("Versions", "DeleteMarkers")
            for item in page.get(key, [])
        ]
        for batch_start in range(0, len(objects), 1000):
            batch = objects[batch_start : batch_start + 1000]
            s3_client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
            deleted += len(batch)
    return deleted


def copy_source_files(s3_client, dest_bucket: str, source_files: list) -> None:
    """Copy each ``(source_bucket, key)`` file into ``dest_bucket``.

    The destination key matches the source key so the ``imap/<instrument>/``
    prefix triggers the EventBridge rule -> indexer lambda -> Dagster.
    """
    for source_bucket, key in source_files:
        print("Copying", key, "from", source_bucket, "to", dest_bucket)
        s3_client.copy_object(
            Bucket=dest_bucket,
            Key=key,
            CopySource={"Bucket": source_bucket, "Key": key},
        )
        time.sleep(1)  # Give the indexer a chance to react before the next file is copied.


def _find_dagster_cluster_arn(ecs_client) -> str:
    """Return the ARN of the Dagster ECS cluster."""
    paginator = ecs_client.get_paginator("list_clusters")
    for page in paginator.paginate():
        for arn in page["clusterArns"]:
            if "Dagster" in arn:
                return arn
    raise RuntimeError("No Dagster ECS cluster found.")


def _find_daemon_service(ecs_client, cluster_arn: str) -> dict:
    """Return the described Dagster daemon service in ``cluster_arn``."""
    paginator = ecs_client.get_paginator("list_services")
    service_arns = [
        arn
        for page in paginator.paginate(cluster=cluster_arn)
        for arn in page["serviceArns"]
    ]
    for batch_start in range(0, len(service_arns), 10):
        batch = service_arns[batch_start : batch_start + 10]
        described = ecs_client.describe_services(cluster=cluster_arn, services=batch)
        for service in described["services"]:
            if "DagsterDaemon" in service["serviceName"]:
                return service
    raise RuntimeError("No Dagster daemon service found.")


def _run_daemon_command(
    ecs_client, command: list, description: str, timeout_seconds: int
) -> None:
    """Run ``command`` as a one-off Fargate task on the Dagster cluster.

    The Dagster RDS instance sits in an isolated subnet with no public route, so
    we cannot connect to it directly. Instead we launch a one-off task that
    reuses the daemon's task definition (and its DB credentials/network config)
    and override the container command. Blocks until the task stops.
    """
    cluster_arn = _find_dagster_cluster_arn(ecs_client)
    daemon_service = _find_daemon_service(ecs_client, cluster_arn)
    task_definition = daemon_service["taskDefinition"]
    network_config = daemon_service["networkConfiguration"]

    run_response = ecs_client.run_task(
        cluster=cluster_arn,
        taskDefinition=task_definition,
        launchType="FARGATE",
        count=1,
        networkConfiguration=network_config,
        overrides={
            "containerOverrides": [
                {"name": DAGSTER_DAEMON_CONTAINER, "command": command}
            ]
        },
    )
    failures = run_response.get("failures", [])
    if failures:
        raise RuntimeError(f"Failed to launch {description} task: {failures}")

    task_arn = run_response["tasks"][0]["taskArn"]
    _wait_for_task(ecs_client, cluster_arn, task_arn, description, timeout_seconds)


def run_dagster_asset_wipe(ecs_client, timeout_seconds: int = 600) -> None:
    """Clear all Dagster asset materializations, preserving dynamic partitions.

    Wiping assets removes materialization/observation events but leaves the
    ``dagster_dynamic_partitions`` table intact.
    """
    _run_daemon_command(
        ecs_client,
        # Pipe the confirmation so the CLI runs non-interactively.
        ["sh", "-c", "echo DELETE | dagster asset wipe --all"],
        "Dagster asset wipe",
        timeout_seconds,
    )


def run_dagster_sensor_cursor_reset(
    ecs_client, suffix: str = KICKOFF_SENSOR_SUFFIX, timeout_seconds: int = 600
) -> None:
    """Clear the stored cursor of every sensor whose name ends in ``suffix``.

    Kickoff sensors store how far they have read through the SDS database and
    Dagster's event log (last-seen storage ids, event ids, and ingestion dates)
    in their cursor. After a table wipe and an asset wipe those markers point
    past everything that now exists, so the sensors would never fire. Clearing
    the cursor makes them re-scan from the beginning. Sensors that do not match
    ``suffix``, and any matching sensor that has no cursor yet, are untouched.
    """
    _run_daemon_command(
        ecs_client,
        ["python", "-c", RESET_SENSOR_CURSORS_SCRIPT.format(suffix=suffix)],
        "Dagster sensor cursor reset",
        timeout_seconds,
    )


def _wait_for_task(
    ecs_client, cluster_arn: str, task_arn: str, description: str, timeout_seconds: int
) -> None:
    """Poll until ``task_arn`` stops, raising on failure or timeout."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        described = ecs_client.describe_tasks(cluster=cluster_arn, tasks=[task_arn])
        task = described["tasks"][0]
        if task["lastStatus"] == "STOPPED":
            for container in task["containers"]:
                exit_code = container.get("exitCode")
                if exit_code not in (0, None):
                    raise RuntimeError(
                        f"{description} task exited with code {exit_code}: "
                        f"{container.get('reason', 'no reason given')}"
                    )
            return
        time.sleep(10)
    raise TimeoutError(
        f"{description} task {task_arn} did not stop within {timeout_seconds}s."
    )
