# test_glows_l1a_sensor.py

# source $(poetry env info --path)/bin/activate
# poetry run pytest tests/orchestration/test_sensors.py 
from sds_data_manager.orchestration.imap_dagster import defs 
from datetime import datetime, timedelta
from sds_data_manager.lambda_code.SDSCode.database import models
import imap_data_access 
from dagster import (
    AssetExecutionContext,
    AssetKey,
    AssetMaterialization,
    DagsterEventType,
    DynamicPartitionsDefinition,
    EventRecordsFilter,
    MaterializeResult,
    build_sensor_context
)


def test_glows_l1a_sensor_trigger(mock_db_session, ephemeral_instance):


    glows_l1a_sensor = defs.get_sensor_def("glows_l1a_all_kickoff_sensor")

    # Mock the Dagster state: Simulate an upstream science file arriving
    # We use 'report_runless_asset_event' to materialize an asset without running a job
    ephemeral_instance.report_runless_asset_event(
        asset_event=AssetMaterialization(
            asset_key=AssetKey(["glows_l0_raw"]),
            partition='repoint2_2026-01-02T00:00:00_to_2026-01-02T23:59:59',
            description="Mocked arrival of L0 science file for testing.",
            metadata={
                    "file_names": ["imap_glows_l0_raw_20260114-repoint00126_v001.pkts"],
                    "input_type": "science",
                    "version": "v001",
                    "start_date": "",
                },
        )
    )

    context = build_sensor_context(instance=ephemeral_instance)
    
    # Run the sensor evaluation
    sensor_result = glows_l1a_sensor(context)

    run_requests = list(sensor_result)
    
    # Verify a run was actually kicked off
    assert len(run_requests) == 1, "Expected exactly one RunRequest to be yielded."
    