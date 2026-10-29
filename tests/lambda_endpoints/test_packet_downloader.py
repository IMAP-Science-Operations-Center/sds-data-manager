"""Tests for the packet downloader lambda."""

import importlib
import logging
from pathlib import Path
from unittest.mock import patch

import imap_data_access
import pytest


@pytest.fixture
def packet_downloader():
    """Import the packet downloader module after the environment is set.

    The module reads ``S3_BUCKET`` and creates boto3 clients at import time,
    so it has to be imported after the autouse ``_set_env`` fixture has run.
    """
    return importlib.import_module(
        "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.packet_downloader"
    )


def _make_stale_files(data_dir: Path):
    """Create files and nested folders like a previous invocation would."""
    (data_dir / "imap_hit_l0_raw_20250101_v001.pkts").write_text("old packets")
    nested = data_dir / "imap" / "hit" / "l0"
    nested.mkdir(parents=True)
    (nested / "imap_hit_l0_raw_20250102_v001.pkts").write_text("old packets")


def test_clear_data_dir(packet_downloader, tmp_path):
    """Files and nested folders are removed, the directory itself is kept."""
    _make_stale_files(tmp_path)

    packet_downloader.clear_data_dir(tmp_path)

    assert tmp_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_clear_data_dir_symlink(packet_downloader, tmp_path):
    """A symlink to a folder is removed without deleting the folder it points to."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep me")
    (data_dir / "link").symlink_to(outside, target_is_directory=True)

    packet_downloader.clear_data_dir(data_dir)

    assert list(data_dir.iterdir()) == []
    assert (outside / "keep.txt").exists()


def test_clear_data_dir_missing_dir(packet_downloader, tmp_path):
    """A directory that does not exist is ignored rather than raising."""
    packet_downloader.clear_data_dir(tmp_path / "does-not-exist")


def test_clear_data_dir_failure_is_logged(packet_downloader, tmp_path, caplog):
    """A file that cannot be removed is logged and does not stop the lambda."""
    (tmp_path / "locked.pkts").write_text("old packets")

    with (
        patch.object(Path, "unlink", side_effect=PermissionError("denied")),
        caplog.at_level(logging.WARNING),
    ):
        packet_downloader.clear_data_dir(tmp_path)

    assert "Could not remove" in caplog.text
    assert "locked.pkts" in caplog.text


def test_lambda_handler_clears_data_dir_before_download(packet_downloader, tmp_path):
    """Stale files are gone by the time the downloads start."""
    _make_stale_files(tmp_path)

    def fake_setup_environment():
        imap_data_access.config["DATA_DIR"] = tmp_path

    def fake_download():
        # Runs where the real download would, so we can check the cleanup
        # already happened before any new data is written.
        assert list(tmp_path.iterdir()) == []

    with (
        # Restore the shared config afterwards so other tests are unaffected
        patch.dict(imap_data_access.config),
        patch.object(
            packet_downloader, "setup_environment", side_effect=fake_setup_environment
        ),
        patch.object(
            packet_downloader, "download_insitu_data", side_effect=fake_download
        ) as mock_download,
    ):
        response = packet_downloader.lambda_handler({}, None)

    mock_download.assert_called_once()
    assert response["statusCode"] == 200
