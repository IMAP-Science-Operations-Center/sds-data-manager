"""Regression tests for spacecraft ephemeris OD priority (issue #1743)."""

import json
from itertools import permutations
from unittest.mock import patch

import pytest

from sds_data_manager.lambda_code.SDSCode import spice_utilities


def _file(filename, kernel_type, timestamp, intervals=None):
    """Build the query metadata used by metakernel selection."""
    return {
        "file_name": f"spk/{filename}",
        "kernel_type": kernel_type,
        "timestamp": timestamp,
        "file_intervals_j2000": intervals if intervals is not None else [[0, 100]],
    }


def _build(files, file_types):
    """Build a metakernel with controlled database query ordering."""

    def query(event, context):
        query_type = event["queryStringParameters"]["type"]
        assert event["queryStringParameters"]["latest"] == "True"
        return {
            "statusCode": 200,
            "body": json.dumps([f for f in files if f["kernel_type"] == query_type]),
        }

    with patch.object(spice_utilities.spice_query_api, "lambda_handler", query):
        return spice_utilities.metakernel_builder(0, 100, file_types=file_types)


@pytest.mark.parametrize("subtype", ["nominal", "predicted", "reconstructed"])
@pytest.mark.parametrize("older_timestamp", [100, 200])
def test_higher_od_wins_regardless_of_row_order(subtype, older_timestamp):
    """A newer OD beats an older solution's higher version and later ingest."""
    prefix = {"nominal": "nom", "predicted": "pred", "reconstructed": "recon"}[subtype]
    kernel_type = f"ephemeris_{subtype}"
    older = _file(
        f"imap_{prefix}_od001_20250926_20280606_v02.bsp",
        kernel_type,
        older_timestamp,
    )
    newer = _file(
        f"imap_{prefix}_od003_20251001_20280606_v01.bsp", kernel_type, 100
    )
    for files in permutations([older, newer]):
        metakernel = _build(files, {kernel_type.upper()})
        assert metakernel.return_spice_files_in_order() == [newer]
        assert not metakernel.contains_gaps()


def test_same_od_uses_ingestion_time_before_filename():
    """Newer ingestion wins within one solution, even with a lower version."""
    older = _file(
        "imap_pred_od003_20251002_20280606_v02.bsp", "ephemeris_predicted", 100
    )
    newer = _file(
        "imap_pred_od003_20251001_20280606_v01.bsp", "ephemeris_predicted", 200
    )
    for files in permutations([older, newer]):
        metakernel = _build(files, {"EPHEMERIS_PREDICTED"})
        assert metakernel.return_spice_files_in_order() == [newer]


def test_exact_od_and_ingestion_ties_are_deterministic():
    """Exact ties have a stable file-name tie breaker, not query ordering."""
    files = [
        _file(f"imap_nom_od003_{date}_20280606_v01.bsp", "ephemeris_nominal", 100)
        for date in ["20251001", "20251002"]
    ]
    for ordered_files in permutations(files):
        metakernel = _build(ordered_files, {"EPHEMERIS_NOMINAL"})
        assert metakernel.return_spice_files_in_order() == [files[1]]


def test_older_od_fills_gaps_and_loads_before_newer_solution():
    """Keep useful older coverage while loading the higher OD last in SPICE."""
    older = _file(
        "imap_nom_od001_20250926_20280606_v02.bsp", "ephemeris_nominal", 200
    )
    newer = _file(
        "imap_nom_od003_20251001_20280606_v01.bsp",
        "ephemeris_nominal",
        100,
        [[10, 40], [50, 100]],
    )
    for files in permutations([older, newer]):
        metakernel = _build(files, {"EPHEMERIS_NOMINAL"})
        assert metakernel.return_spice_files_in_order() == [older, newer]
        assert not metakernel.contains_gaps()


def test_ephemeris_type_priority_is_preserved():
    """Reconstructed still beats nominal, which still beats predicted."""
    reconstructed = _file(
        "imap_recon_od001_20251001_20251002_v01.bsp",
        "ephemeris_reconstructed",
        100,
        [[20, 80]],
    )
    nominal = _file(
        "imap_nom_od003_20251001_20280606_v01.bsp", "ephemeris_nominal", 200
    )
    predicted = _file(
        "imap_pred_od005_20251001_20280606_v01.bsp", "ephemeris_predicted", 300
    )
    for files in permutations([reconstructed, nominal, predicted]):
        metakernel = _build(
            files,
            {"EPHEMERIS_RECONSTRUCTED", "EPHEMERIS_NOMINAL", "EPHEMERIS_PREDICTED"},
        )
        assert metakernel.return_spice_files_in_order() == [nominal, reconstructed]
        assert not metakernel.contains_gaps()


def test_legacy_ephemeris_without_od_keeps_timestamp_priority():
    """Unnumbered kernels remain usable and newer ingestion wins among them."""
    older = _file("imap_long_20250926_20280606_v02.bsp", "ephemeris_long", 100)
    newer = _file("imap_long_20251001_20280606_v01.bsp", "ephemeris_long", 200)
    for files in permutations([older, newer]):
        metakernel = _build(files, {"EPHEMERIS_LONG"})
        assert metakernel.return_spice_files_in_order() == [newer]


def test_numbered_ephemeris_precedes_legacy_files():
    """An unnumbered file fills only coverage missing from a numbered OD."""
    legacy = _file("imap_nom_20250926_20280606_v02.bsp", "ephemeris_nominal", 200)
    numbered = _file(
        "imap_nom_od000_20251001_20280606_v01.bsp",
        "ephemeris_nominal",
        100,
        [[10, 100]],
    )
    for files in permutations([legacy, numbered]):
        metakernel = _build(files, {"EPHEMERIS_NOMINAL"})
        assert metakernel.return_spice_files_in_order() == [legacy, numbered]
        assert not metakernel.contains_gaps()


@pytest.mark.parametrize(
    ("filename", "expected_od"),
    [
        ("spk/imap_nom_od009_20251001_20280606_v01.bsp", 9),
        ("spk/imap_nom_od010_20251001_20280606_v01.bsp", 10),
        ("spk/IMAP_NOM_OD003_20251001_20280606_V01.BSP", 3),
        ("/data/_od999_/imap_long_20251001_20280606_v01.bsp", -1),
    ],
)
def test_od_is_numeric_case_insensitive_and_from_basename(filename, expected_od):
    """Match supported case variants without mistaking directory names for OD."""
    priority = spice_utilities._ephemeris_priority(
        {"file_name": filename, "timestamp": 100}
    )
    assert priority[0] == expected_od


@pytest.mark.parametrize(
    "kernel_type", ["attitude_history", "planetary_ephemeris", "spin", "thruster"]
)
def test_non_spacecraft_ephemeris_keeps_timestamp_priority(kernel_type):
    """OD-like names must not change another category's existing priority."""
    older = _file("older_od999_file", kernel_type, 100)
    newer = _file("newer_od001_file", kernel_type, 200)
    for files in permutations([older, newer]):
        metakernel = _build(files, {kernel_type.upper()})
        assert metakernel.return_spice_files_in_order() == [newer]
