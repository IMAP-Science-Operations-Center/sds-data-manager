"""Tests for internals to the spin module."""

import datetime

import pytest

from sds_data_manager.orchestration.spin import CoverageInterval


def test_coverage_interval_ordering():
    # disjoint
    ivl1 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 1, 1))
    ivl2 = CoverageInterval(datetime.date(2026, 1, 31), datetime.date(2026, 2, 1))
    assert ivl1 < ivl2
    assert ivl1 <= ivl2
    assert ivl2 > ivl1
    assert ivl2 >= ivl1
    assert ivl1 != ivl2

    # overlapping
    ivl1 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 2, 1))
    ivl2 = CoverageInterval(datetime.date(2026, 1, 31), datetime.date(2026, 3, 1))
    assert ivl1 < ivl2

    # touching
    ivl1 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 2, 1))
    ivl2 = CoverageInterval(datetime.date(2026, 2, 1), datetime.date(2026, 3, 1))
    assert ivl1 < ivl2

    # enveloping
    ivl1 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 2, 1))
    ivl2 = CoverageInterval(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))
    assert ivl1 < ivl2

    # same start date
    ivl1 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 1, 1))
    ivl2 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 2, 1))
    assert ivl1 < ivl2

    # same start and end date
    ivl1 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 1, 1))
    ivl2 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 1, 1))
    assert ivl1 == ivl2


def test_coverage_interval_bounds_ordering():
    # disjoint
    ivl1 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 1, 1))
    ivl2 = CoverageInterval(datetime.date(2026, 1, 31), datetime.date(2026, 2, 1))
    assert ivl1.bounds_le(ivl2)
    assert ivl2.bounds_ge(ivl1)
    assert not ivl1.envelops(ivl2)
    assert not ivl2.envelops(ivl1)

    # overlapping
    ivl1 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 2, 1))
    ivl2 = CoverageInterval(datetime.date(2026, 1, 31), datetime.date(2026, 3, 1))
    assert not ivl1.bounds_le(ivl2)
    assert not ivl2.bounds_ge(ivl1)
    assert not ivl1.envelops(ivl2)
    assert not ivl2.envelops(ivl1)

    # touching
    ivl1 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 2, 1))
    ivl2 = CoverageInterval(datetime.date(2026, 2, 1), datetime.date(2026, 3, 1))
    assert ivl1.bounds_le(ivl2)
    assert ivl2.bounds_ge(ivl1)
    assert not ivl1.envelops(ivl2)
    assert not ivl2.envelops(ivl1)

    # enveloping
    ivl1 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 2, 1))
    ivl2 = CoverageInterval(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))
    assert not ivl1.bounds_le(ivl2)
    assert not ivl2.bounds_ge(ivl1)
    assert ivl1.envelops(ivl2)
    assert not ivl2.envelops(ivl1)

    # same start date
    ivl1 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 1, 1))
    ivl2 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 2, 1))
    assert not ivl1.bounds_le(ivl2)
    assert not ivl2.bounds_ge(ivl1)
    assert not ivl1.envelops(ivl2)
    assert ivl2.envelops(ivl1)

    # same start and end date
    ivl1 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 1, 1))
    ivl2 = CoverageInterval(datetime.date(2025, 12, 31), datetime.date(2026, 1, 1))
    assert not ivl1.bounds_le(ivl2)
    assert not ivl2.bounds_ge(ivl1)
    assert ivl1.envelops(ivl2)
    assert ivl2.envelops(ivl1)


@pytest.mark.parametrize(
    ("bounds", "expected_bounds"),
    [
        pytest.param([], [], id="empty"),
        pytest.param([(1, 3)], [(1, 3)], id="single"),
        pytest.param(
            [(1, 2), (4, 5), (7, 8)],
            [(1, 2), (4, 5), (7, 8)],
            id="disjoint",
        ),
        pytest.param([(1, 3), (3, 5)], [(1, 5)], id="touching"),
        pytest.param([(1, 4), (3, 6)], [(1, 6)], id="overlapping"),
        pytest.param([(1, 8), (3, 4)], [(1, 8)], id="contained"),
        pytest.param([(1, 3), (1, 3)], [(1, 3)], id="duplicate"),
        pytest.param([(1, 3), (1, 6)], [(1, 6)], id="same-start"),
        pytest.param([(1, 4), (3, 6), (5, 8)], [(1, 8)], id="chained-overlap"),
        pytest.param(
            [(1, 4), (3, 6), (8, 9)],
            [(1, 6), (8, 9)],
            id="overlap-then-gap",
        ),
    ],
)
def test_coverage_interval_merging(bounds, expected_bounds):
    intervals = [
        CoverageInterval(datetime.date(2026, 1, start), datetime.date(2026, 1, end))
        for start, end in bounds
    ]
    expected = [
        (datetime.date(2026, 1, start), datetime.date(2026, 1, end))
        for start, end in expected_bounds
    ]

    merged = CoverageInterval.merge_sorted(intervals)
    assert [(interval.start, interval.end) for interval in merged] == expected
