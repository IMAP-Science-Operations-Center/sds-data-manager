"""End-to-end checks that the seeded files flow through the pipeline.

Each test asserts that some expected artifact of the pipeline exists. As the
pipeline grows, add more source files in ``conftest.py`` and more tests here so
a run reports exactly which products succeeded and which failed.
"""

import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from sds_data_manager.lambda_code.SDSCode.database.models import ScienceFiles


def _wait_for_file_path(engine, file_path, timeout_seconds, poll_seconds=5):
    """Return True once ``file_path`` appears in ``science_files``, else False."""
    session_factory = sessionmaker(bind=engine)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with session_factory() as session:
            found = session.execute(
                select(ScienceFiles.file_path).where(
                    ScienceFiles.file_path == file_path
                )
            ).scalar_one_or_none()
        if found is not None:
            return True
        time.sleep(poll_seconds)
    return False


@pytest.mark.parametrize(
    ("file_path", "timeout"),
    [
        (
            "imap/glows/l0/2026/01/imap_glows_l0_raw_20260101-repoint00096_v001.0002.pkts",
            60,
        ),
        (
            "imap/glows/l1a/2026/01/imap_glows_l1a_hist_20260101-repoint00096_v001.0001.cdf",
            600,
        ),
        (
            "imap/glows/l1b/2026/01/imap_glows_l1b_hist_20260101-repoint00096_v001.0001.cdf",
            600,
        ),
        (
            "imap/glows/l2/2026/01/imap_glows_l2_hist_20260101-repoint00096_v001.0001.cdf",
            600,
        ),
        (
            "imap/glows/l3a/2026/01/imap_glows_l3a_hist_20260101-repoint00096_v001.0001.cdf",
            600,
        ),
    ],
)
def test_glows_files_generated(sds_db_engine, file_path, timeout):
    """Check that the GLOWS science file is indexed into the database."""
    print(f"Waiting for {file_path} for up to {timeout} seconds...")
    assert _wait_for_file_path(sds_db_engine, file_path, timeout_seconds=timeout), (
        f"{file_path} was not indexed within {timeout} seconds."
    )
