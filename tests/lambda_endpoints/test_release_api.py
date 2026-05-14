"""Integration tests for the Release API.

These tests verify the six core release behaviors against a live in-memory DB:

Test Case 1  release (no descriptor)  — all matching instrument+date files released
Test Case 2  release (with descriptor) — only the matching descriptor subset released
Test Case 3  early-release (no descriptor) — same bulk outcome as Test Case 1
Test Case 4  early-release (with descriptor) — same selective outcome as Test Case 2
Test Case 5  unrelease (no descriptor) — all matching files unreleased
Test Case 6  unrelease (with descriptor) — only matching descriptor files unreleased
"""

import datetime

from sds_data_manager.lambda_code.SDSCode.api_lambdas import release_api
from sds_data_manager.lambda_code.SDSCode.database import models

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_event(query_params, scope="full"):
    """Build a minimal API Gateway event for the release API."""
    return {
        "queryStringParameters": query_params,
        "rawPath": "/api-key/release",
        "body": "",
        "requestContext": {
            "authorizer": {"lambda": {"apiKey": "test-key", "scope": scope}}
        },
    }


def _required(
    instrument="hit",
    start_date="20250101",
    end_date="20250131",
    release_type="release",
):
    """Return a minimal valid query-param dict."""
    return {
        "instrument": instrument,
        "start_date": start_date,
        "end_date": end_date,
        "release_type": release_type,
    }


def _science(
    session,
    *,
    file_path,
    instrument="hit",
    descriptor="hk",
    start_date="20250115",
    released=False,
):
    session.add(
        models.ScienceFiles(
            file_path=file_path,
            instrument=instrument,
            data_level="l0",
            descriptor=descriptor,
            start_date=datetime.datetime.strptime(start_date, "%Y%m%d"),
            version="v001",
            extension="pkts",
            released=released,
            ingestion_date=datetime.datetime(
                2025, 1, 20, 0, 0, 0, tzinfo=datetime.timezone.utc
            ),
        )
    )
    session.commit()


# ---------------------------------------------------------------------------
# Test Case 1  release — all files for instrument + date range
# ---------------------------------------------------------------------------


def test_release_all_files_in_date_range(session):
    """release_type=release with no descriptor releases every matching file.

    Two files for the target instrument within the date range and one file
    outside the range must remain unreleased.
    """
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250110",
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts",
        instrument="hit",
        descriptor="sci",
        start_date="20250120",
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250201_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250201",
    )  # outside range

    result = release_api.lambda_handler(
        event=_build_event(_required(start_date="20250101", end_date="20250131")),
        context={},
    )

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.ScienceFiles).all()}
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts"] is True, (
        "In-range file should be released"
    )
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts"] is True, (
        "In-range file should be released"
    )
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250201_v001.pkts"] is False, (
        "Out-of-range file must stay unreleased"
    )


# ---------------------------------------------------------------------------
# Test Case 2  release — only the matching descriptor
# ---------------------------------------------------------------------------


def test_release_specific_descriptor(session):
    """release_type=release with descriptor releases only that product.

    Two files share the same instrument and date range but have different
    descriptors; only the specified descriptor must be released.
    """
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250115_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250115",
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_sci_20250115_v001.pkts",
        instrument="hit",
        descriptor="sci",
        start_date="20250115",
    )

    params = _required()
    params["descriptor"] = "hk"
    result = release_api.lambda_handler(event=_build_event(params), context={})

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.ScienceFiles).all()}
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250115_v001.pkts"] is True, (
        "Specified descriptor must be released"
    )
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250115_v001.pkts"] is False, (
        "Other descriptor must remain unreleased"
    )


# ---------------------------------------------------------------------------
# Test Case 3  early-release — all files for instrument + date range
# ---------------------------------------------------------------------------


def test_early_release_all_files_in_date_range(session):
    """release_type=early-release with no descriptor releases all matching files.

    Behaviour must be identical to Test Case 1 but without requiring release_number.
    """
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250110",
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts",
        instrument="hit",
        descriptor="sci",
        start_date="20250120",
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250201_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250201",
    )

    result = release_api.lambda_handler(
        event=_build_event(
            _required(
                start_date="20250101", end_date="20250131", release_type="early-release"
            )
        ),
        context={},
    )

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.ScienceFiles).all()}
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts"] is True
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts"] is True
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250201_v001.pkts"] is False


# ---------------------------------------------------------------------------
# Test Case 4  early-release — only the matching descriptor
# ---------------------------------------------------------------------------


def test_early_release_specific_descriptor(session):
    """release_type=early-release with descriptor releases only that product."""
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250115_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250115",
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_sci_20250115_v001.pkts",
        instrument="hit",
        descriptor="sci",
        start_date="20250115",
    )

    params = _required(release_type="early-release")
    params["descriptor"] = "hk"
    result = release_api.lambda_handler(event=_build_event(params), context={})

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.ScienceFiles).all()}
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250115_v001.pkts"] is True
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250115_v001.pkts"] is False


# ---------------------------------------------------------------------------
# Test Case 5  unrelease — all files for instrument + date range
# ---------------------------------------------------------------------------


def test_unrelease_all_files_in_date_range(session):
    """release_type=unrelease clears released flag on all matching files.

    Pre-populate files as released=True; after the call they must be False.
    Files outside the range must remain released=True.
    """
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250110",
        released=True,
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts",
        instrument="hit",
        descriptor="sci",
        start_date="20250120",
        released=True,
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250201_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250201",
        released=True,
    )  # outside range

    result = release_api.lambda_handler(
        event=_build_event(
            _required(
                start_date="20250101", end_date="20250131", release_type="unrelease"
            )
        ),
        context={},
    )

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.ScienceFiles).all()}
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts"] is False, (
        "In-range file must be unreleased"
    )
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts"] is False, (
        "In-range file must be unreleased"
    )
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250201_v001.pkts"] is True, (
        "Out-of-range file must remain released"
    )


# ---------------------------------------------------------------------------
# Test Case 6  unrelease — only the matching descriptor
# ---------------------------------------------------------------------------


def test_unrelease_specific_descriptor(session):
    """release_type=unrelease with descriptor only clears that descriptor.

    Both files start as released=True; only the specified descriptor should
    be unreleased after the call.
    """
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250115_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250115",
        released=True,
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_sci_20250115_v001.pkts",
        instrument="hit",
        descriptor="sci",
        start_date="20250115",
        released=True,
    )

    params = _required(release_type="unrelease")
    params["descriptor"] = "hk"
    result = release_api.lambda_handler(event=_build_event(params), context={})

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.ScienceFiles).all()}
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250115_v001.pkts"] is False, (
        "Specified descriptor must be unreleased"
    )
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250115_v001.pkts"] is True, (
        "Other descriptor must remain released"
    )
