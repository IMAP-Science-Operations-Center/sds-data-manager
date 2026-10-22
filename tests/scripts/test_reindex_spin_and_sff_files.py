"""Tests for the spin and small forces file backfill script."""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from imap_data_access import SPICEFilePath
from moto import mock_s3
from sqlalchemy import select

from scripts.spice import reindex_spin_and_sff_files
from sds_data_manager.lambda_code.SDSCode.database import models
from tests.conftest import in_memory_session

TEST_SPICE_DATA_DIR = Path(__file__).parent.parent / "test-data" / "test_spice_files"
BUCKET_NAME = "test-data-bucket"
SPIN_PATH = "imap/spice/spin/imap_2026_267_2026_267_01.spin.csv"
SFF_PATH = "imap/spice/activities/imap_2026_273_2026_273_hist_01.sff"
INGESTION_DATE = datetime(2025, 1, 1, 10, 0, 0)


@pytest.fixture(autouse=True)
def _set_env(monkeypatch, tmpdir):
    """Point the script at a mocked bucket and database."""
    monkeypatch.setenv("S3_BUCKET", BUCKET_NAME)
    monkeypatch.setenv("SECRET_NAME", "test-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("DATA_DIR", str(tmpdir))


@pytest.fixture
def s3_client():
    """Mock S3 with an empty data bucket."""
    with mock_s3():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET_NAME)
        yield client


@pytest.fixture
def session():
    """Create a test database session (in-memory SQLite)."""
    with in_memory_session() as session:
        yield session


def _put_file(s3_client, s3_path):
    """Upload a test file to the mocked bucket."""
    local_path = TEST_SPICE_DATA_DIR / Path(s3_path).name
    s3_client.put_object(Bucket=BUCKET_NAME, Key=s3_path, Body=local_path.read_bytes())


@pytest.fixture
def time_kernels(session, s3_client):
    """Make the leapseconds and spacecraft clock kernels available to furnish."""
    for s3_path in (
        "imap/spice/lsk/naif0012.tls",
        "imap/spice/sclk/imap_sclk_0012.tsc",
    ):
        _put_file(s3_client, s3_path)
        filename = Path(s3_path).name
        version = SPICEFilePath(filename).spice_metadata["version"]
        session.add(
            models.SPICEFiles(
                file_path=s3_path,
                file_name=filename,
                file_root="".join(filename.rsplit(version, 1)),
                kernel_type=SPICEFilePath(filename).spice_metadata["type"],
                version=version,
                min_date_j2000=0,
                max_date_j2000=1000000000,
                file_intervals_j2000=[[0, 1000000000]],
                min_date_datetime=datetime(2000, 1, 1),
                max_date_datetime=datetime(2031, 9, 9),
                ingestion_date=INGESTION_DATE,
            )
        )
    session.commit()


def _last_modified(s3_client, s3_path):
    """Return a file's S3 last modified time, which the indexers record."""
    return s3_client.head_object(Bucket=BUCKET_NAME, Key=s3_path)["LastModified"]


@pytest.fixture
def legacy_rows(session, s3_client):
    """Index a spin file and a small forces file only into their own tables."""
    _put_file(s3_client, SPIN_PATH)
    _put_file(s3_client, SFF_PATH)
    session.add(
        models.SpinFiles(
            file_path=SPIN_PATH,
            start_date=datetime(2026, 9, 24),
            end_date=datetime(2026, 9, 24),
            version="01",
            ingestion_date=_last_modified(s3_client, SPIN_PATH),
        )
    )
    session.add(
        models.SmallForcesFile(
            file_path=SFF_PATH,
            start_date=datetime(2026, 9, 30),
            end_date=datetime(2026, 9, 30),
            version="01",
            ingestion_date=_last_modified(s3_client, SFF_PATH),
        )
    )
    session.commit()


def _spice_rows(session):
    """Return the spin and thruster SPICE table rows as comparable tuples."""
    table = models.SPICEFiles
    query = (
        select(
            table.file_path,
            table.kernel_type,
            table.ingestion_date,
            table.min_date_datetime,
            table.max_date_datetime,
        )
        .where(table.kernel_type.in_(["spin", "thruster"]))
        .order_by(table.file_path)
    )
    return session.execute(query).all()


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.get_file_ingestion_date"
)
def test_reindex_files(
    mock_get_ingestion_date, session, time_kernels, legacy_rows, capsys
):
    """Files are indexed with their legacy row's ingestion date and real coverage."""
    assert reindex_spin_and_sff_files.main([]) == 0
    # The S3 last modified time is never read
    mock_get_ingestion_date.assert_not_called()

    rows = _spice_rows(session)
    assert [(row.file_path, row.kernel_type) for row in rows] == [
        (SFF_PATH, "thruster"),
        (SPIN_PATH, "spin"),
    ]
    legacy_dates = {
        SPIN_PATH: session.get(models.SpinFiles, SPIN_PATH).ingestion_date,
        SFF_PATH: session.get(models.SmallForcesFile, SFF_PATH).ingestion_date,
    }
    assert all(row.ingestion_date == legacy_dates[row.file_path] for row in rows)
    assert "spin: 0 files have a different ingestion_date" in capsys.readouterr().out
    # Coverage comes from the files, not the filename dates
    assert rows[0].min_date_datetime == datetime(2026, 9, 30, 13, 2, 6, 662376)
    assert rows[1].max_date_datetime == datetime(2026, 9, 24, 16, 41, 2)

    # A second run changes nothing
    assert reindex_spin_and_sff_files.main([]) == 0
    assert _spice_rows(session) == rows


def test_reindex_one_type(session, time_kernels, legacy_rows):
    """--type limits the backfill to one file type."""
    assert reindex_spin_and_sff_files.main(["--type", "spin"]) == 0
    assert [row.kernel_type for row in _spice_rows(session)] == ["spin"]


def test_reindex_dry_run(session, legacy_rows, capsys):
    """A dry run reads every file but writes nothing.

    No time kernels are needed, since coverage isn't converted to SPICE times.
    """
    assert reindex_spin_and_sff_files.main(["--dry-run"]) == 0

    assert _spice_rows(session) == []
    output = capsys.readouterr().out
    assert "spin: read 1 of 1 files, 0 failed" in output
    assert "thruster: read 1 of 1 files, 0 failed" in output


def test_reindex_reports_failures(
    session, time_kernels, legacy_rows, s3_client, capsys
):
    """A file that can't be re-indexed is reported, and the script exits with 1."""
    s3_client.delete_object(Bucket=BUCKET_NAME, Key=SPIN_PATH)

    assert reindex_spin_and_sff_files.main([]) == 1

    output = capsys.readouterr().out
    assert "spin: re-indexed 0 of 1 files, 1 failed" in output
    assert f"FAILED {SPIN_PATH}" in output
    assert f"MISSING {SPIN_PATH}" in output
    # The other file type is still re-indexed
    assert "thruster: re-indexed 1 of 1 files, 0 failed" in output


def _spice_ingestion_date(session, file_path):
    """Return a file's ingestion date in the SPICE table."""
    session.expire_all()
    return session.execute(
        select(models.SPICEFiles.ingestion_date).where(
            models.SPICEFiles.file_path == file_path
        )
    ).scalar_one()


def test_reindex_keeps_date_when_s3_differs(session, time_kernels, legacy_rows):
    """The legacy row's date is kept when the S3 object's time has changed.

    That happens if the object was rewritten after it was first indexed, e.g.
    copied or restored.
    """
    session.get(models.SpinFiles, SPIN_PATH).ingestion_date = INGESTION_DATE
    session.commit()

    for _ in range(2):  # a second run keeps it too
        assert reindex_spin_and_sff_files.main([]) == 0
        assert _spice_ingestion_date(session, SPIN_PATH) == INGESTION_DATE


def test_reindex_repairs_wrong_date(session, time_kernels, legacy_rows):
    """Re-running fixes a SPICE table row written with the wrong date.

    The legacy row's date differs from the S3 object's time, so only copying
    the legacy date repairs the row.
    """
    session.get(models.SpinFiles, SPIN_PATH).ingestion_date = INGESTION_DATE
    session.commit()
    assert reindex_spin_and_sff_files.main([]) == 0
    session.execute(
        models.SPICEFiles.__table__.update()
        .where(models.SPICEFiles.file_path == SPIN_PATH)
        .values(ingestion_date=datetime(2030, 1, 1))
    )
    session.commit()

    assert reindex_spin_and_sff_files.main([]) == 0
    assert _spice_ingestion_date(session, SPIN_PATH) == INGESTION_DATE


def test_compare_with_legacy_table(session, time_kernels, legacy_rows):
    """Missing rows and rows with a different ingestion date are both found."""
    assert reindex_spin_and_sff_files.main(["--type", "thruster"]) == 0
    session.get(models.SmallForcesFile, SFF_PATH).ingestion_date = INGESTION_DATE
    session.commit()

    assert reindex_spin_and_sff_files.compare_with_legacy_table("spin") == (
        [SPIN_PATH],
        [],
    )
    assert reindex_spin_and_sff_files.compare_with_legacy_table("thruster") == (
        [],
        [SFF_PATH],
    )


def test_reindex_requires_environment(monkeypatch):
    """The script refuses to run without a database secret and bucket."""
    monkeypatch.delenv("SECRET_NAME")
    with pytest.raises(SystemExit):
        reindex_spin_and_sff_files.main(["--dry-run"])
