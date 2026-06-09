"""Tests for pipeline lambda types."""

from datetime import datetime

import pytest

from sds_data_manager.orchestration.types import (
    DependencyNode,
    ProcessingJobNode,
    TimeRange,
    get_cadence_duration,
)

# ---------------------------------------------------------------------------
# TimeRange
# ---------------------------------------------------------------------------


def test_time_range_from_string():
    tr = TimeRange.from_string(
        "20250101", "20250131", pointing_number_start=3, pointing_number_end=5
    )
    assert tr.pointing_number_start == 3
    assert tr.pointing_number_end == 5
    assert tr.start_time == datetime(2025, 1, 1)
    assert tr.end_time == datetime(2025, 1, 31)

    tr = TimeRange.from_string("20250101", "20250131")
    assert tr.pointing_number_start is None
    assert tr.pointing_number_end is None

    start, end = "20250101", "20250131"
    tr = TimeRange.from_string(start, end)
    assert tr.to_string() == (start, end)


def test_node_valid_construction():
    node = DependencyNode(source="swe", data_type="l1a", descriptor="sci")
    assert node.source == "swe"
    assert node.data_type == "l1a"
    assert node.descriptor == "sci"


def test_node_invalid_raises():
    with pytest.raises(ValueError, match="Invalid data source"):
        DependencyNode(source="not_an_instrument", data_type="l1a", descriptor="sci")
    with pytest.raises(ValueError, match="Invalid data type"):
        DependencyNode(source="swe", data_type="l9z", descriptor="sci")

    with pytest.raises(ValueError, match="Descriptor must be a non-empty string"):
        DependencyNode(source="swe", data_type="l1a", descriptor="")

    with pytest.raises(ValueError, match="Descriptor must be a non-empty string"):
        DependencyNode(source="swe", data_type="l1a", descriptor="   ")


# ---------------------------------------------------------------------------
# DependencyNode defaults and boolean validation
# ---------------------------------------------------------------------------


def test_dependency_node_defaults():
    node = DependencyNode(source="mag", data_type="l1b", descriptor="norm")
    assert node.required is True
    assert node.trigger_job is True
    assert node.dependency_query_time_range == []


def test_dependency_node_non_boolean_required_raises():
    with pytest.raises(ValueError, match="must be boolean"):
        DependencyNode(source="swe", data_type="l1a", descriptor="sci", required="yes")


def test_dependency_node_non_boolean_kickoff_raises():
    with pytest.raises(ValueError, match="must be boolean"):
        DependencyNode(source="swe", data_type="l1a", descriptor="sci", trigger_job=1)


# ---------------------------------------------------------------------------
# DependencyNode dependency_query_time_range validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dependency_query_time_range",
    [
        ["-3p", "3p"],
        ["-3d", "5d"],
        ["-2h", "2h"],
        ["-1l"],
        ["6np"],
        ["6nd"],
    ],
)
def test_dependency_node_valid_time_ranges(dependency_query_time_range):
    node = DependencyNode(
        source="swe",
        data_type="l1a",
        descriptor="sci",
        dependency_query_time_range=dependency_query_time_range,
    )
    assert node.dependency_query_time_range == dependency_query_time_range


def test_dependency_node_nearest_with_future_raises():
    with pytest.raises(ValueError, match="Nearest need to be in this format"):
        DependencyNode(
            source="swe",
            data_type="l1a",
            descriptor="sci",
            dependency_query_time_range=["6np", "3p"],
        )


def test_dependency_node_positive_past_raises():
    with pytest.raises(ValueError, match="must be negative"):
        DependencyNode(
            source="swe",
            data_type="l1a",
            descriptor="sci",
            dependency_query_time_range=["3d", "5d"],
        )


def test_dependency_node_negative_future_raises():
    with pytest.raises(ValueError, match="be positive"):
        DependencyNode(
            source="swe",
            data_type="l1a",
            descriptor="sci",
            dependency_query_time_range=["-3d", "-5d"],
        )


def test_dependency_node_too_many_elements_raises():
    with pytest.raises(ValueError, match="1-2 elements"):
        DependencyNode(
            source="swe",
            data_type="l1a",
            descriptor="sci",
            dependency_query_time_range=["-3d", "5d", "1d"],
        )


def test_dependency_node_serialize_roundtrip():
    node = DependencyNode(
        source="mag",
        data_type="l1b",
        descriptor="norm",
        required=False,
        trigger_job=True,
        dependency_query_time_range=["-2d", "2d"],
    )
    serialized = node.serialize()
    restored = DependencyNode.deserialize(serialized)
    assert restored == node


def test_processing_job_node_construction():
    node = ProcessingJobNode(
        source="glows",
        data_type="l1a",
        descriptor="hist",
        time_span=TimeRange.from_string("20240101", "20240131"),
    )
    assert node.source == "glows"
    assert node.reprocessing is False


def test_processing_job_node_with_pointing():
    node = ProcessingJobNode(
        source="glows",
        data_type="l1a",
        descriptor="hist",
        time_span=TimeRange.from_string(
            "20240101", "20240101", pointing_number_start=4
        ),
    )
    assert node.time_span.pointing_number_start == 4


def test_processing_job_node_inherits_node_validation():
    with pytest.raises(ValueError, match="Invalid data source"):
        ProcessingJobNode(
            source="invalid",
            data_type="l1a",
            descriptor="hist",
            time_span=TimeRange.from_string("20240101", "20240101"),
        )


@pytest.mark.parametrize(
    ("descriptor", "expected"),
    [
        ("swe-sci-1mo", "1mo"),
        ("glows-hist-3mo", "3mo"),
        ("mag-norm-6mo", "6mo"),
        ("hit-sci-1yr", "1yr"),
    ],
)
def test_get_cadence_duration_valid(descriptor, expected):
    assert get_cadence_duration(descriptor) == expected


@pytest.mark.parametrize(
    "descriptor",
    [
        "swe-sci",
        "glows-hist-de",
        "mag-norm-all",
        "hit-sci-2mo",
    ],
)
def test_get_cadence_duration_returns_none(descriptor):
    assert get_cadence_duration(descriptor) is None
