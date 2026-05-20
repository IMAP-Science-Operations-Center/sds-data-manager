"""Tests for orchestration/glows.py module."""

from unittest.mock import MagicMock, patch

import pytest
from dagster import AssetSpec, DynamicPartitionsDefinition

import orchestration.glows as glows
from orchestration.imap_file import IMAPAncillaryFileHandler, IMAPScienceFileHandler
from orchestration.imap_job import IMAPJobHandler


# ---------------------------------------------------------------------------
# ancillary_files
# ---------------------------------------------------------------------------

EXPECTED_ANCILLARY_DESCRIPTORS = [
    "pipeline-settings",
    "l1b-conversion-table-for-anc-data",
    "l1b-exclusions-by-instr-team",
    "l1b-map-of-excluded-regions",
    "l1b-map-of-uv-sources",
    "l1b-suspected-transients",
    "l2-calibration",
    "time-dep-bckgrd",
    "map-of-extra-helio-bckgrd",
    "l3a-map-of-extra-helio-bckgrd",
    "l3a-time-dep-bckgrd",
    "calibration-data",
]


def test_ancillary_files_count():
    assert len(glows.ancillary_files) == len(EXPECTED_ANCILLARY_DESCRIPTORS)


def test_ancillary_files_are_correct_type():
    for handler in glows.ancillary_files:
        assert isinstance(handler, IMAPAncillaryFileHandler)


def test_ancillary_files_asset_names():
    names = [h.asset_name for h in glows.ancillary_files]
    for descriptor in EXPECTED_ANCILLARY_DESCRIPTORS:
        assert f"glows_ancillary_{descriptor}" in names


def test_ancillary_files_source_and_data_type():
    for handler in glows.ancillary_files:
        assert handler.source == "glows"
        assert handler.data_type == "ancillary"


def test_ancillary_files_no_spice_spin_attitude():
    for handler in glows.ancillary_files:
        assert handler.needs_spice is False
        assert handler.needs_spin is False
        assert handler.needs_pointing_attitude is False


# ---------------------------------------------------------------------------
# l0_files
# ---------------------------------------------------------------------------

def test_l0_files_count():
    assert len(glows.l0_files) == 1


def test_l0_files_are_correct_type():
    assert isinstance(glows.l0_files[0], IMAPScienceFileHandler)


def test_l0_files_asset_name():
    assert glows.l0_files[0].asset_name == "glows_l0_raw"


def test_l0_files_partition():
    assert isinstance(glows.l0_files[0].partitions_def, DynamicPartitionsDefinition)
    assert glows.l0_files[0].partitions_def.name == "repoint_partitions"


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

EXPECTED_JOB_NAMES = [
    "glows_l1a_all",
    "glows_l1b_de",
    "glows_l1b_hist",
    "glows_l2_hist",
    "glows_l3a_hist",
]


def test_jobs_count():
    assert len(glows.jobs) == len(EXPECTED_JOB_NAMES)


def test_jobs_are_correct_type():
    for job in glows.jobs:
        assert isinstance(job, IMAPJobHandler)


def test_jobs_asset_names():
    # IMAPJobHandler strips hyphens from asset_name
    names = [j.asset_name for j in glows.jobs]
    for expected in EXPECTED_JOB_NAMES:
        assert expected.replace("-", "") in names


def test_jobs_partition():
    for job in glows.jobs:
        assert isinstance(job.partitions_def, DynamicPartitionsDefinition)
        assert job.partitions_def.name == "repoint_partitions"


# ---------------------------------------------------------------------------
# assets_to_build
# ---------------------------------------------------------------------------

def test_assets_to_build_count():
    expected = len(glows.ancillary_files) + len(glows.l0_files) + len(glows.jobs)
    assert len(glows.assets_to_build) == expected


def test_assets_to_build_order():
    assert glows.assets_to_build[: len(glows.ancillary_files)] == glows.ancillary_files
    assert glows.assets_to_build[len(glows.ancillary_files) : len(glows.ancillary_files) + 1] == glows.l0_files
    assert glows.assets_to_build[len(glows.ancillary_files) + 1 :] == glows.jobs


# ---------------------------------------------------------------------------
# built asset lists
# ---------------------------------------------------------------------------

def test_batch_jobs_non_empty():
    assert len(glows.batch_jobs) == len(glows.assets_to_build)


def test_batch_jobs_are_asset_specs():
    for asset in glows.batch_jobs:
        assert isinstance(asset, AssetSpec)


def test_assets_list_non_empty():
    assert len(glows.assets) > 0


def test_sensors_count():
    # One sensor per handler in assets_to_build
    assert len(glows.sensors) == len(glows.assets_to_build)
