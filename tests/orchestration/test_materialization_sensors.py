"""Tests for the science file and job output backup materialization sensors."""

import datetime

from dagster import AssetKey, AssetMaterialization, build_sensor_context

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import config
from sds_data_manager.orchestration.backup_checker import (
    _parse_cursor,
)
from sds_data_manager.orchestration.imap_dagster import defs

GLOWS_L0_PARTITION = "repoint{}_2026-01-0{}T00:00:00_to_2026-01-0{}T23:59:59"


def _science_file(
    file_path,
    data_level,
    descriptor,
    ingestion_date,
    repointing=2,
    major_version=1,
    minor_version=1,
):
    """Return a GLOWS ScienceFiles row for the given repointing."""
    return models.ScienceFiles(
        file_path=file_path,
        instrument="glows",
        data_level=data_level,
        descriptor=descriptor,
        start_date=datetime.datetime(2026, 1, repointing),
        repointing=repointing,
        major_version=major_version,
        minor_version=minor_version,
        ingestion_date=ingestion_date,
        cr=1,
        crid="asdf",
        released=False,
        extension="cdf" if data_level != "l0" else "pkts",
    )


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def test_backup_sensor_only_materializes_old_missed_outputs(
    mock_db_session, ephemeral_instance
):
    """The backup sensor waits for min_age and skips outputs the op materialized."""
    min_age = config.BACKUP_MATERIALIZATION_MIN_AGE
    de_path = "imap_glows_l1a_de_20260102_v001.0001.cdf"
    hist_path = "imap_glows_l1a_hist_20260102_v001.0001.cdf"
    de_file = _science_file(
        de_path,
        "l1a",
        "de",
        _now() - min_age - datetime.timedelta(minutes=30),
    )
    hist_file = _science_file(
        hist_path,
        "l1a",
        "hist",
        _now() - datetime.timedelta(minutes=5),
    )
    mock_db_session.add_all([de_file, hist_file])
    # Old enough, but already materialized by the processing job op
    mock_db_session.add(
        _science_file(
            "imap_glows_l1a_de_20260103_v001.0001.cdf",
            "l1a",
            "de",
            _now() - min_age - datetime.timedelta(minutes=45),
            repointing=3,
        )
    )
    mock_db_session.commit()
    ephemeral_instance.report_runless_asset_event(
        AssetMaterialization(
            asset_key=AssetKey(["glows_l1a_de"]),
            partition=GLOWS_L0_PARTITION.format(3, 3, 3),
            metadata={
                "file_names": ["imap_glows_l1a_de_20260103_v001.0001.cdf"],
                "input_type": "science",
                "major_version": "1",
                "minor_version": "1",
            },
        )
    )

    backup_sensor = defs.get_sensor_def("job_output_backup_materialization_sensor")
    result = backup_sensor(build_sensor_context(instance=ephemeral_instance))

    # Only the old, un-materialized de file. The hist file is too new.
    assert len(result.asset_events) == 1
    event = result.asset_events[0]
    assert event.asset_key == AssetKey(["glows_l1a_de"])
    assert event.metadata["file_names"].value == [de_path]
    assert event.metadata["materialized_by"].value == "backup_sensor"

    # Once the hist file ages past min_age it is picked up; the cursor did not
    # skip over it while it was too new.
    mock_db_session.query(models.ScienceFiles).filter(
        models.ScienceFiles.file_path == hist_path
    ).update({"ingestion_date": _now() - min_age - datetime.timedelta(minutes=1)})
    mock_db_session.commit()
    result = backup_sensor(
        build_sensor_context(instance=ephemeral_instance, cursor=result.cursor)
    )
    assert len(result.asset_events) == 1
    assert result.asset_events[0].metadata["file_names"].value == [hist_path]


def _report_materialization(instance, asset_name, partition, file_names):
    """Record a runless materialization, as the op or another sensor would."""
    instance.report_runless_asset_event(
        AssetMaterialization(
            asset_key=AssetKey([asset_name]),
            partition=partition,
            metadata={
                "file_names": file_names,
                "input_type": "science",
                "major_version": "1",
                "minor_version": "1",
            },
        )
    )


def test_backup_sensor_does_not_overwrite_other_files(
    mock_db_session, ephemeral_instance
):
    """Only empty partitions or newer versions of the same file are materialized."""
    ingested = (
        _now() - config.BACKUP_MATERIALIZATION_MIN_AGE - datetime.timedelta(minutes=30)
    )
    multi_file_path = "imap_glows_l1a_de_20260102_v002.0001.cdf"
    other_file_path = "imap_glows_l1a_de_20260103_v002.0001.cdf"
    new_version_path = "imap_glows_l1a_de_20260104_v002.0001.cdf"
    mock_db_session.add_all(
        [
            _science_file(path, "l1a", "de", ingested, repointing=repoint)
            for repoint, path in [
                (2, multi_file_path),
                (3, other_file_path),
                (4, new_version_path),
            ]
        ]
    )
    mock_db_session.commit()

    # Partition holding several files (like idex_l0_raw): must not be replaced
    # with a single file.
    _report_materialization(
        ephemeral_instance,
        "glows_l1a_de",
        GLOWS_L0_PARTITION.format(2, 2, 2),
        [
            "imap_glows_l1a_de_20260102_v001.0001.cdf",
            "imap_glows_l1a_de_20260102-extra_v001.0001.cdf",
        ],
    )
    # Partition holding a different single file: must not be replaced.
    _report_materialization(
        ephemeral_instance,
        "glows_l1a_de",
        GLOWS_L0_PARTITION.format(3, 3, 3),
        ["imap_glows_l1a_de_20260102_v001.0001.cdf"],
    )
    # Partition holding an older version of the same file: upgraded.
    _report_materialization(
        ephemeral_instance,
        "glows_l1a_de",
        GLOWS_L0_PARTITION.format(4, 4, 4),
        ["imap_glows_l1a_de_20260104_v001.0001.cdf"],
    )

    backup_sensor = defs.get_sensor_def("job_output_backup_materialization_sensor")
    result = backup_sensor(build_sensor_context(instance=ephemeral_instance))

    assert len(result.asset_events) == 1
    event = result.asset_events[0]
    assert event.partition == GLOWS_L0_PARTITION.format(4, 4, 4)
    assert event.metadata["file_names"].value == [new_version_path]


def test_backup_sensor_ignores_files_older_than_max_age(
    mock_db_session, ephemeral_instance
):
    """A missing or stale cursor never reaches back past max_age."""
    old_path = "imap_glows_l1a_de_20260102_v001.0001.cdf"
    mock_db_session.add(
        _science_file(
            old_path,
            "l1a",
            "de",
            _now()
            - config.BACKUP_MATERIALIZATION_MAX_AGE
            - datetime.timedelta(minutes=5),
        )
    )
    mock_db_session.commit()

    backup_sensor = defs.get_sensor_def("job_output_backup_materialization_sensor")
    # No cursor (e.g. a reset Dagster instance) and a stale cursor both start
    # the scan at max_age.
    stale_cursor = (_now() - datetime.timedelta(days=1)).isoformat()
    for cursor in [None, stale_cursor]:
        result = backup_sensor(
            build_sensor_context(instance=ephemeral_instance, cursor=cursor)
        )
        assert result.asset_events == []


def test_parse_cursor():
    """Cursors may be empty, JSON, or a legacy bare ISO timestamp."""
    start, path = _parse_cursor(None)
    assert start == datetime.datetime(2025, 9, 24, tzinfo=datetime.timezone.utc)
    assert path == ""

    date, path = _parse_cursor(
        '{"ingestion_date": "2026-01-02T00:00:00-06:00", "file_path": "a.cdf"}'
    )
    assert date == datetime.datetime(2026, 1, 2, 6, tzinfo=datetime.timezone.utc)
    assert path == "a.cdf"

    date, path = _parse_cursor("2026-01-02T00:00:00")
    assert date == datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
    assert path == ""
