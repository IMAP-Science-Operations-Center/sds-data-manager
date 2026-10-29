"""Test the custom idex L0 sensor."""

import datetime

from dagster import DagsterInstance, Definitions, asset, build_sensor_context

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration.custom_behavior.idex import IDEXL0FileHandler
from sds_data_manager.orchestration.custom_partitions import idex10_partitions
from sds_data_manager.orchestration.imap_dagster import dependency_config


def test_idex_l0_sensor(mock_db_session):
    """Mock a sensor run and check that an IDEX l0 run is requested."""
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(
        "idex_10_day_partitions",
        [
            "daily_2026-01-01T00:00:00_to_2026-01-10T00:00:00",
            "daily_2026-01-10T00:00:00_to_2026-01-20T00:00:00",
        ],
    )

    # Set up upstream assets for idex pipeline
    @asset(partitions_def=idex10_partitions)
    def idex_l0_raw():
        pass

    defs = Definitions(assets=[idex_l0_raw])
    repo_def = defs.get_repository_def()

    previous_ingestion_date = datetime.datetime(
        2026, 1, 1, tzinfo=datetime.timezone.utc
    )
    context = build_sensor_context(
        instance=instance,
        repository_def=repo_def,
        cursor=previous_ingestion_date.isoformat(),
    )
    file_1_ingestion_date = previous_ingestion_date + datetime.timedelta(days=1)
    # Insert multiple mock IDEX l0 records into the IDEX table
    # The ingestion date is a day later than the previous ingestion date.
    idex_l0_file1 = models.IDEXL0Files(
        file_path="imap_idex_l0_raw_20260102_v001.0001.cdf",
        start_date=datetime.datetime(
            2026, 1, 1
        ),  # Start of an IDEX fixed 10-day window
        major_version=1,
        minor_version=1,
        ingestion_date=file_1_ingestion_date,
    )
    file_2_ingestion_date = file_1_ingestion_date + datetime.timedelta(days=10)
    idex_l0_file2 = models.IDEXL0Files(
        file_path="imap_idex_l0_raw_20260111_v001.0001.cdf",
        start_date=datetime.datetime(2026, 1, 10),  # Start of the next window
        major_version=1,
        minor_version=1,
        ingestion_date=file_2_ingestion_date,
    )
    mock_db_session.add_all([idex_l0_file1, idex_l0_file2])
    mock_db_session.commit()

    inputs = dependency_config.inputs(("idex", "l1a", "all"))
    node = next(input for input in inputs if input.source == "idex")
    sensor = IDEXL0FileHandler(node, idex10_partitions).build_sensor()
    result = sensor(context)
    assert result is not None
    # Check that the new curser is the latest ingestion date (from the second file)
    assert result.cursor == file_2_ingestion_date.isoformat()
    # Check that there are two runs requested since we inserted 2 new files
    # with ingestion dates greater than the most recent one.
    assert len(result.run_requests) == 2
