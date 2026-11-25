"""Tests for reprocess_jobs_for_major_version_bump.py."""

import importlib.metadata
from pathlib import Path
from unittest.mock import patch

import pytest
from imap_data_access.dependencies import (
    DependencyConfigReader,
    clear_config_cache,
)

from scripts.dependency import reprocess_jobs_for_major_version_bump as script
from scripts.dependency.reprocess_jobs_for_major_version_bump import (
    reprocess_jobs_for_major_version_bump,
)

REPO_ROOT = Path(__file__).parents[2]

PYPI_LOCK = """
[[package]]
name = "boto3"
version = "1.0.0"

[[package]]
name = "imap-data-access"
version = "1.4.0.dev0"
"""

GIT_LOCK = """
[[package]]
name = "imap-data-access"
version = "1.3.0"

[package.source]
type = "git"
url = "https://github.com/IMAP-Science-Operations-Center/imap-data-access.git"
reference = "v1.4.0dev"
resolved_reference = "a6e2d37129627fce4f6da37d459c736113e285a2"
"""


def _checkout(tmp_path: Path, lock: str | None = None, in_repo_yaml=False) -> Path:
    """Make a fake checkout with a poetry.lock and, optionally, in-repo YAML."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    if lock is not None:
        (checkout / "poetry.lock").write_text(lock)
    if in_repo_yaml:
        (checkout / script.IN_REPO_YAML_DIR).mkdir(parents=True)
    return checkout


def test_baseline_requirement_for_a_pypi_release(tmp_path):
    """A PyPI pin becomes an exact version requirement."""
    lock = _checkout(tmp_path, PYPI_LOCK) / "poetry.lock"
    assert script.baseline_requirement(lock) == "imap-data-access==1.4.0.dev0"


def test_baseline_requirement_for_a_git_pin(tmp_path):
    """A git pin becomes a requirement for the exact commit, not the tag."""
    lock = _checkout(tmp_path, GIT_LOCK) / "poetry.lock"
    assert script.baseline_requirement(lock) == (
        "imap-data-access @ git+https://github.com/IMAP-Science-Operations-Center/"
        "imap-data-access.git@a6e2d37129627fce4f6da37d459c736113e285a2"
    )


def test_baseline_requirement_without_imap_data_access(tmp_path):
    """A lock file without imap-data-access is an error."""
    lock = _checkout(tmp_path, '[[package]]\nname = "boto3"\n') / "poetry.lock"
    with pytest.raises(ValueError, match="imap-data-access is not in"):
        script.baseline_requirement(lock)


def test_baseline_requirement_matches_the_installed_version():
    """The repository's own poetry.lock resolves to the version installed here."""
    version = importlib.metadata.version("imap-data-access")
    assert script.baseline_requirement(REPO_ROOT / "poetry.lock") == (
        f"imap-data-access=={version}"
    )


def test_previous_yaml_dir_before_the_move(tmp_path):
    """A commit that still has the YAML in this repository uses that copy."""
    checkout = _checkout(tmp_path, PYPI_LOCK, in_repo_yaml=True)
    with patch.object(script, "_pip_install") as pip_install:
        yaml_dir = script.previous_yaml_dir(checkout, tmp_path / "work")
    assert yaml_dir == checkout / script.IN_REPO_YAML_DIR
    pip_install.assert_not_called()


def test_previous_yaml_dir_after_the_move(tmp_path):
    """Otherwise the YAML comes from the imap-data-access pinned in that commit."""
    checkout = _checkout(tmp_path, PYPI_LOCK)
    work_dir = tmp_path / "work"

    def fake_install(requirement, target):
        (target / script.PACKAGED_YAML_DIR).mkdir(parents=True)

    with patch.object(script, "_pip_install", side_effect=fake_install) as install:
        yaml_dir = script.previous_yaml_dir(checkout, work_dir)
    install.assert_called_once_with("imap-data-access==1.4.0.dev0", work_dir)
    assert yaml_dir == work_dir / script.PACKAGED_YAML_DIR


def test_previous_yaml_dir_without_packaged_yaml(tmp_path):
    """A pinned version that predates the packaged YAML is an error."""
    checkout = _checkout(tmp_path, PYPI_LOCK)
    with (
        patch.object(script, "_pip_install"),
        pytest.raises(FileNotFoundError, match="no packaged dependency YAML"),
    ):
        script.previous_yaml_dir(checkout, tmp_path / "work")


def test_reprocess_jobs_for_major_version_bump():
    """A single changed leaf job should be the only one reprocessed."""
    old_reader = DependencyConfigReader()
    clear_config_cache()
    new_reader = DependencyConfigReader()

    new_reader.config[("idex", "l1b", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2a", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-10days")].outputs[1].major_version += 1
    jobs = reprocess_jobs_for_major_version_bump(old_reader, new_reader)

    assert jobs == [("idex", "l1b", "sci-10days")]


def test_reprocess_jobs_for_major_version_bump_root_node():
    """When the kickoff job itself changed, it should be the reprocessed root."""
    old_reader = DependencyConfigReader()
    clear_config_cache()
    new_reader = DependencyConfigReader()

    new_reader.config[("idex", "l1a", "all")].outputs[0].major_version += 1
    new_reader.config[("idex", "l1b", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2a", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-10days")].outputs[1].major_version += 1
    jobs = reprocess_jobs_for_major_version_bump(old_reader, new_reader)

    assert jobs == [("idex", "l1a", "all")]


def test_reprocess_jobs_for_major_version_bump_two_upstream():
    """A node with two changed upstream branches should reprocess both roots."""
    old_reader = DependencyConfigReader()
    clear_config_cache()
    new_reader = DependencyConfigReader()

    new_reader.config[("idex", "l1b", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l1b", "msg-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2a", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-10days")].outputs[1].major_version += 1
    jobs = reprocess_jobs_for_major_version_bump(old_reader, new_reader)

    assert jobs == [
        ("idex", "l1b", "msg-10days"),
        ("idex", "l1b", "sci-10days"),
    ]


def test_reprocess_jobs_for_major_version_bump_multiple_instruments():
    """Changes in independent instrument pipelines should each get their own root."""
    old_reader = DependencyConfigReader()
    clear_config_cache()
    new_reader = DependencyConfigReader()

    # IDEX
    new_reader.config[("idex", "l1b", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2a", "sci-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-10days")].outputs[0].major_version += 1
    new_reader.config[("idex", "l2b", "all-10days")].outputs[1].major_version += 1

    # SWE
    new_reader.config[("swe", "l1a", "all")].outputs[0].major_version += 1
    new_reader.config[("swe", "l1b", "sci")].outputs[0].major_version += 1
    new_reader.config[("swe", "l2", "sci")].outputs[0].major_version += 1

    jobs = reprocess_jobs_for_major_version_bump(old_reader, new_reader)

    assert jobs == [
        ("idex", "l1b", "sci-10days"),
        ("swe", "l1a", "all"),
    ]
