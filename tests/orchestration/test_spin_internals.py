"""Tests for internals to the spin module."""

import datetime

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
