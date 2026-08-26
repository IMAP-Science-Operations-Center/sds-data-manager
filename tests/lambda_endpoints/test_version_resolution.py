"""Tests for the latest version resolution query builder."""

import datetime
import json

from sds_data_manager.lambda_code.SDSCode.api_lambdas import query_api
from sds_data_manager.lambda_code.SDSCode.database import models


def _add_science(
    session,
    *,
    major,
    minor,
    descriptor="count",
    start_date="20251107",
    repointing=None,
    released=True,
):
    """Add one science file, defaulting everything but the version/series keys."""
    session.add(
        models.ScienceFiles(
            file_path=(
                f"test/file/path/imap_hit_l1a_{descriptor}_{start_date}_"
                f"v{major:03d}.{minor:04d}_{repointing}.cdf"
            ),
            instrument="hit",
            data_level="l1a",
            descriptor=descriptor,
            start_date=datetime.datetime.strptime(start_date, "%Y%m%d"),
            repointing=repointing,
            major_version=major,
            minor_version=minor,
            extension="cdf",
            ingestion_date=datetime.datetime(2025, 11, 7, 10, 13, 12),
            released=released,
        )
    )
    session.commit()


def _returned(returned_query, *fields):
    """Return the set of per-file tuples of ``fields`` in a 200 response."""
    assert returned_query["statusCode"] == 200
    return {
        tuple(item[field] for field in fields)
        for item in json.loads(returned_query["body"])
    }


def test_latest_major_resolved_per_series(session):
    """The default query keeps each series' own latest major, not a global one."""
    for major, minor in [(1, 1), (2, 1), (2, 2)]:
        _add_science(session, descriptor="count", major=major, minor=minor)
    for major, minor in [(1, 1), (1, 2), (3, 1)]:
        _add_science(session, descriptor="rates", major=major, minor=minor)

    result = query_api.lambda_handler(
        event={"queryStringParameters": {"instrument": "hit"}}, context={}
    )

    # A global "latest major" would drop the count series (max major 2 < 3).
    assert _returned(result, "descriptor", "major_version", "minor_version") == {
        ("count", 2, 1),
        ("count", 2, 2),
        ("rates", 3, 1),
    }


def test_latest_true_resolved_per_series(session):
    """latest=true returns the single newest file of every matching series."""
    for major, minor in [(1, 1), (2, 1), (2, 2)]:
        _add_science(session, descriptor="count", major=major, minor=minor)
    for major, minor in [(1, 1), (1, 2), (3, 1)]:
        _add_science(session, descriptor="rates", major=major, minor=minor)

    result = query_api.lambda_handler(
        event={"queryStringParameters": {"instrument": "hit", "latest": "true"}},
        context={},
    )

    assert _returned(result, "descriptor", "major_version", "minor_version") == {
        ("count", 2, 2),
        ("rates", 3, 1),
    }


def test_repointing_defines_series_with_null_safe_grouping(session):
    """Distinct repointings are distinct series; NULL repointings group together."""
    # Same instrument/level/descriptor/start_date; only repointing differs.
    _add_science(session, repointing=None, major=1, minor=1)
    _add_science(session, repointing=None, major=2, minor=1)
    _add_science(session, repointing=100, major=1, minor=1)
    _add_science(session, repointing=100, major=1, minor=2)
    _add_science(session, repointing=200, major=3, minor=1)

    result = query_api.lambda_handler(
        event={"queryStringParameters": {"instrument": "hit"}}, context={}
    )

    # The NULL series resolves to major 2 (proving the two NULL rows grouped);
    # repointing 100 keeps both minors of major 1; repointing 200 keeps major 3.
    assert _returned(result, "repointing", "major_version", "minor_version") == {
        (None, 2, 1),
        (100, 1, 1),
        (100, 1, 2),
        (200, 3, 1),
    }


def test_start_date_partitions_series_and_prefilters(session):
    """Each start_date is its own series, and a date range drops whole days."""
    for start_date, major, minor in [
        ("20260407", 1, 1),
        ("20260407", 1, 2),
        ("20260407", 2, 2),
        ("20260408", 1, 1),
        ("20260408", 1, 2),
    ]:
        _add_science(session, start_date=start_date, major=major, minor=minor)

    # Without a date filter, each day resolves its own latest independently.
    result = query_api.lambda_handler(
        event={"queryStringParameters": {"instrument": "hit", "latest": "true"}},
        context={},
    )
    assert _returned(result, "start_date", "major_version", "minor_version") == {
        ("20260407", 2, 2),
        ("20260408", 1, 2),
    }

    # A date range drops the 8th entirely; the 7th's winner is unchanged.
    result = query_api.lambda_handler(
        event={
            "queryStringParameters": {
                "instrument": "hit",
                "latest": "true",
                "start_date": "20260407",
                "end_date": "20260407",
            }
        },
        context={},
    )
    assert _returned(result, "start_date", "major_version", "minor_version") == {
        ("20260407", 2, 2),
    }


def test_non_partition_filter_applied_before_ranking(session):
    """A filter on a non-series column narrows candidates before ranking."""
    # Newest file is (major 2, minor 2); minor 1 exists only on older files.
    for major, minor in [(1, 1), (2, 1), (2, 2)]:
        _add_science(session, major=major, minor=minor)

    result = query_api.lambda_handler(
        event={
            "queryStringParameters": {
                "instrument": "hit",
                "latest": "true",
                "minor_version": "1",
            }
        },
        context={},
    )

    # minor_version=1 is applied before the window, so ranking sees only
    # {(1, 1), (2, 1)} and (2, 1) wins -- "the latest among minor-1 files".
    assert _returned(result, "major_version", "minor_version") == {(2, 1)}


def test_unreleased_newer_version_does_not_hide_released_latest(session):
    """An unauthenticated caller still gets the latest *released* version."""
    _add_science(session, major=1, minor=1, released=True)
    _add_science(session, major=2, minor=1, released=False)

    result = query_api.lambda_handler(
        event={"queryStringParameters": {"instrument": "hit"}}, context={}
    )

    # If `released` were filtered after the window instead of before it,
    # unreleased major 2 would rank newest and then be dropped, wrongly
    # hiding the series entirely.
    assert _returned(result, "major_version", "minor_version") == {(1, 1)}


def test_authenticated_ranks_over_unreleased(session):
    """An authenticated caller resolves latest over unreleased versions too."""
    _add_science(session, major=1, minor=1, released=True)
    _add_science(session, major=2, minor=1, released=True)
    _add_science(session, major=3, minor=1, released=False)

    result = query_api.lambda_handler(
        event={
            "rawPath": "/authorized/query",
            "queryStringParameters": {"instrument": "hit"},
        },
        context={},
    )

    # Unreleased major 3 is the newest and wins for an authenticated caller.
    assert _returned(result, "major_version", "minor_version") == {(3, 1)}
