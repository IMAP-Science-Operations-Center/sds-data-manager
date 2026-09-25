"""Regression tests for reprocessing jobs that produce ancillary outputs.

See issue #1655: ``IMAPJobHandler.find_outputs()`` used to query the
``science_files`` table for *every* configured output, including ancillary
ones. Because ``"ancillary"`` is not a member of the Postgres ``data_level``
enum, filtering ``science_files.data_level == "ancillary"`` raised
``sqlalchemy.exc.DataError`` and crashed the Dagster op. Ancillary outputs must
instead be looked up in the ``ancillary_files`` table.
"""

import datetime

from dagster import MaterializeResult, build_asset_context

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration.imap_dagster import dependency_config
from sds_data_manager.orchestration.imap_job import IMAPJobHandler

# The GLOWS l3b job from the issue: it emits both science (l3b/l3c/l3d/l3e)
# and ancillary (e.g. l3b-archive) outputs.
JOB_KEY = ("glows", "l3b", "ion-rate-profile")
PARTITION_KEY = "daily_2026-09-20T00:00:00_to_2026-09-21T00:00:00"
START_DATE = datetime.datetime(2026, 9, 20)
ARCHIVE_FILE = "imap_glows_l3b-archive_20260920_v002.zip"


def _insert_l3b_archive(session, version="v002"):
    """Insert the GLOWS l3b-archive ancillary file the job just produced."""
    session.add(
        models.AncillaryFiles(
            file_path=f"imap/ancillary/glows/{ARCHIVE_FILE}",
            instrument="glows",
            descriptor="l3b-archive",
            start_date=START_DATE,
            end_date=None,
            version=version,
            extension="zip",
            released=False,
            ingestion_date=datetime.datetime(2026, 9, 21),
        )
    )
    session.commit()


def test_find_outputs_materializes_ancillary_output(mock_db_session):
    """find_outputs() finds an ancillary output without crashing on the enum.

    Reproduces the exact path from issue #1655: reprocessing GLOWS l3b for a
    single day. Previously this raised ``DataError`` because the ancillary
    output was queried against ``science_files``.
    """
    _insert_l3b_archive(mock_db_session)

    job_handler = IMAPJobHandler(dependency_config._config[JOB_KEY])
    context = build_asset_context(partition_key=PARTITION_KEY)

    # Must not raise (the bug raised sqlalchemy.exc.DataError here).
    materializations = job_handler.find_outputs(
        context,
        mock_db_session,
        start_date=START_DATE,
    )

    ancillary = [
        m for m in materializations if m.metadata["file_names"][0] == ARCHIVE_FILE
    ]
    assert len(ancillary) == 1
    result = ancillary[0]
    assert isinstance(result, MaterializeResult)
    assert result.asset_key.to_python_identifier() == "glows_ancillary_l3barchive"
    assert result.metadata["input_type"] == "ancillary"
    # Major version is config-driven (2); minor comes from the file's "v002".
    assert result.metadata["major_version"] == "2"
    assert result.metadata["minor_version"] == "2"


def test_find_outputs_ancillary_matches_produced_minor_version(mock_db_session):
    """When output_versions is provided, the bumped minor version is matched.

    Two versions of the same ancillary file exist; passing the produced minor
    version selects that exact "vXXX" row rather than the latest.
    """
    _insert_l3b_archive(mock_db_session, version="v002")
    _insert_l3b_archive_other = models.AncillaryFiles(
        file_path="imap/ancillary/glows/imap_glows_l3b-archive_20260920_v003.zip",
        instrument="glows",
        descriptor="l3b-archive",
        start_date=START_DATE,
        end_date=None,
        version="v003",
        extension="zip",
        released=False,
        ingestion_date=datetime.datetime(2026, 9, 22),
    )
    mock_db_session.add(_insert_l3b_archive_other)
    mock_db_session.commit()

    job_handler = IMAPJobHandler(dependency_config._config[JOB_KEY])
    context = build_asset_context(partition_key=PARTITION_KEY)

    materializations = job_handler.find_outputs(
        context,
        mock_db_session,
        output_versions={"l3b-archive": {"major_version": 2, "minor_version": 2}},
        start_date=START_DATE,
    )

    ancillary = [m for m in materializations if m.metadata["input_type"] == "ancillary"]
    assert len(ancillary) == 1
    assert ancillary[0].metadata["file_names"][0] == ARCHIVE_FILE
    assert ancillary[0].metadata["minor_version"] == "2"
