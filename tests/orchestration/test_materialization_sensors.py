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
        _now() - min_age - datetime.timedelta(hours=1),
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
            _now() - min_age - datetime.timedelta(hours=2),
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
