"""Tests for SPICE dependency trigger narrowing."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dagster import DagsterInstance, build_sensor_context

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration.dependency import DependencyConfigReader
from sds_data_manager.orchestration.imap_job import IMAPJobHandler


def _mag_job_handler():
    """Return a job handler with ephemeris dependencies."""
    reader = DependencyConfigReader()
    return IMAPJobHandler(reader.config[("mag", "l1d", "norm-srf")])


def _session_returning(new_files, predecessor):
    """Return a mock session for trigger range testing."""
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = new_files
    first = session.query.return_value.filter.return_value.order_by.return_value.first
    first.return_value = predecessor
    return session


def _spice_file(file_name, min_dt, max_dt, intervals):
    """Build a lightweight SPICE file object for tests."""
    return SimpleNamespace(
        file_name=file_name,
        min_date_datetime=min_dt,
        max_date_datetime=max_dt,
        file_intervals_datetime=intervals,
        ingestion_date=datetime(2025, 1, 5),
    )


def test_ephemeris_reconstructed_only_triggers_new_daily_partitions():
    """Ephemeris reconstructed kernels should only trigger for new coverage."""
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(
        "daily_partitions",
        [
            "daily_2025-01-01T00:00:00_to_2025-01-02T00:00:00",
            "daily_2025-01-02T00:00:00_to_2025-01-03T00:00:00",
            "daily_2025-01-03T00:00:00_to_2025-01-04T00:00:00",
        ],
    )
    context = build_sensor_context(instance=instance)

    job_handler = _mag_job_handler()
    dependency = next(
        dep
        for dep in job_handler.job_config.spice_inputs
        if dep.source == "ephemeris_reconstructed"
    )

    min_dt = datetime(2025, 1, 1)
    old_max = datetime(2025, 1, 2)
    new_max = datetime(2025, 1, 4)
    predecessor = _spice_file(
        "imap_2025_001_2025_002_01.bsp",
        min_dt,
        old_max,
        [[min_dt, old_max]],
    )
    new_file = _spice_file(
        "imap_2025_001_2025_004_01.bsp",
        min_dt,
        new_max,
        [[min_dt, old_max], [old_max, new_max]],
    )
    session = _session_returning([new_file], predecessor)
    cursors = {dependency.to_dagster_name(): "2024-01-01T00:00:00"}

    with patch(
        "sds_data_manager.orchestration.imap_job.db.Session"
    ) as mock_session_cls:
        mock_session_cls.return_value.__enter__.return_value = session
        mock_session_cls.return_value.__exit__.return_value = False
        partitions = job_handler.trigger_from_new_non_science_inputs(
            context,
            dependency,
            cursors,
            models.SPICEFiles,
            models.SPICEFiles.kernel_type,
            None,
            "min_date_datetime",
            "max_date_datetime",
        )

    assert set(partitions) == {
        "daily_2025-01-02T00:00:00_to_2025-01-03T00:00:00",
        "daily_2025-01-03T00:00:00_to_2025-01-04T00:00:00",
    }


def test_predicted_ephemeris_does_not_trigger_partitions():
    """Predicted ephemeris kernels should not trigger reprocessing."""
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(
        "daily_partitions",
        ["daily_2025-01-01T00:00:00_to_2025-01-02T00:00:00"],
    )
    context = build_sensor_context(instance=instance)

    job_handler = _mag_job_handler()
    dependency = next(
        dep
        for dep in job_handler.job_config.spice_inputs
        if dep.source == "ephemeris_predicted"
    )

    new_file = _spice_file(
        "imap_2025_001_2025_002_01_pred.bsp",
        datetime(2025, 1, 1),
        datetime(2025, 1, 2),
        [[datetime(2025, 1, 1), datetime(2025, 1, 2)]],
    )
    session = _session_returning([new_file], None)
    cursors = {dependency.to_dagster_name(): "2024-01-01T00:00:00"}

    with patch(
        "sds_data_manager.orchestration.imap_job.db.Session"
    ) as mock_session_cls:
        mock_session_cls.return_value.__enter__.return_value = session
        mock_session_cls.return_value.__exit__.return_value = False
        partitions = job_handler.trigger_from_new_non_science_inputs(
            context,
            dependency,
            cursors,
            models.SPICEFiles,
            models.SPICEFiles.kernel_type,
            None,
            "min_date_datetime",
            "max_date_datetime",
        )

    assert partitions == []
