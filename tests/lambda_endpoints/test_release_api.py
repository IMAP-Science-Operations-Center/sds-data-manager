"""Tests for the Release API."""

import datetime
import json

from sds_data_manager.lambda_code.SDSCode.api_lambdas import release_api
from sds_data_manager.lambda_code.SDSCode.database import models


def _build_event(query_params, raw_path="/api-key/release"):
    """Build an API event payload for release_api tests."""
    return {"queryStringParameters": query_params, "rawPath": raw_path, "body": ""}


def _insert_release_record(
    session,
    *,
    file_path,
    instrument="swe",
    descriptor="withhold-data-release-001",
    start_date="20250101",
    end_date="20250331",
    version="v001",
    released=True,
    ingestion_date="2025-04-09 21:12:53+00:00",
):
    """Insert one release-table record."""
    session.add(
        models.ReleaseFiles(
            file_path=file_path,
            instrument=instrument,
            descriptor=descriptor,
            start_date=datetime.datetime.strptime(start_date, "%Y%m%d"),
            end_date=datetime.datetime.strptime(end_date, "%Y%m%d"),
            version=version,
            extension="txt",
            ingestion_date=datetime.datetime.strptime(
                ingestion_date, "%Y-%m-%d %H:%M:%S%z"
            ),
            released=released,
        )
    )
    session.commit()


def test_query_result_body(session):
    """Tests that the query result body can be loaded."""
    _insert_release_record(
        session,
        file_path=(
            "release/imap_swe_withhold-data-release-001_20250101_20250331_v001.txt"
        ),
    )
    event = _build_event({})

    returned_query = release_api.lambda_handler(event=event, context={})

    assert json.loads(returned_query["body"])


def test_invalid_query_parameter(session):
    """Test invalid parameters return a 400 status with explanation."""
    _insert_release_record(
        session,
        file_path=(
            "release/imap_swe_withhold-data-release-001_20250101_20250331_v001.txt"
        ),
    )
    event = _build_event({"size": "500"})

    returned_query = release_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 400
    assert "size is not a valid query parameter" in returned_query["body"]


def test_release_type_filter_contains(session):
    """Test release_type applies a contains filter against file_path."""
    _insert_release_record(
        session,
        file_path=(
            "release/imap_swe_withhold-data-release-001_20250101_20250331_v001.txt"
        ),
        descriptor="withhold-data-release-001",
        version="v001",
    )
    _insert_release_record(
        session,
        file_path="release/imap_swe_early-release_20250101_20250331_v002.txt",
        descriptor="early-release",
        version="v002",
    )

    event = _build_event({"release_type": "early-release"})
    returned_query = release_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    body = json.loads(returned_query["body"])
    assert len(body) == 1
    assert body[0]["descriptor"] == "early-release"


def test_invalid_release_type_value(session):
    """Test invalid release_type values return 400."""
    _insert_release_record(
        session,
        file_path=(
            "release/imap_swe_withhold-data-release-001_20250101_20250331_v001.txt"
        ),
    )

    event = _build_event({"release_type": "not-a-type"})
    returned_query = release_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 400
    assert "is not a valid release_type parameter" in returned_query["body"]


def test_invalid_start_date_format_returns_400(session):
    """Test invalid start_date format is rejected."""
    _insert_release_record(
        session,
        file_path=(
            "release/imap_swe_withhold-data-release-001_20250101_20250331_v001.txt"
        ),
    )

    event = _build_event({"start_date": "2025-01-01"})
    returned_query = release_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 400
    assert "Invalid value for start_date" in returned_query["body"]


def test_start_and_end_date_filters(session):
    """Test start_date and end_date query filtering."""
    _insert_release_record(
        session,
        file_path=(
            "release/imap_swe_withhold-data-release-001_20250101_20250331_v001.txt"
        ),
        start_date="20250101",
        end_date="20250331",
        version="v001",
    )
    _insert_release_record(
        session,
        file_path=(
            "release/imap_swe_withhold-data-release-001_20250401_20250630_v002.txt"
        ),
        start_date="20250401",
        end_date="20250630",
        version="v002",
    )

    event = _build_event({"start_date": "20250301", "end_date": "20250701"})
    returned_query = release_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    body = json.loads(returned_query["body"])
    assert len(body) == 1
    assert body[0]["version"] == "v002"


def test_returns_only_latest_version_after_filters(session):
    """Test query returns the highest version from filtered rows."""
    _insert_release_record(
        session,
        file_path=(
            "release/imap_swe_withhold-data-release-001_20250101_20250331_v001.txt"
        ),
        version="v001",
    )
    _insert_release_record(
        session,
        file_path=(
            "release/imap_swe_withhold-data-release-001_20250101_20250331_v002.txt"
        ),
        version="v002",
    )

    event = _build_event(
        {
            "instrument": "swe",
            "release_type": "withhold-data",
            "start_date": "20250101",
            "end_date": "20250331",
        }
    )
    returned_query = release_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    body = json.loads(returned_query["body"])
    assert len(body) == 1
    assert body[0]["version"] == "v002"


def test_unauthenticated_requests_return_400(session):
    """Test non-authenticated requests are rejected by release API."""
    _insert_release_record(
        session,
        file_path=(
            "release/imap_swe_withhold-data-release-001_20250101_20250331_v001.txt"
        ),
        version="v001",
        released=True,
    )
    _insert_release_record(
        session,
        file_path=(
            "release/imap_swe_withhold-data-release-001_20250101_20250331_v002.txt"
        ),
        version="v002",
        released=False,
    )

    unauth_event = _build_event(
        {"release_type": "withhold-data"}, raw_path="/public/release"
    )

    unauth_response = release_api.lambda_handler(event=unauth_event, context={})

    assert unauth_response["statusCode"] == 400
    assert "API authentication failed" in unauth_response["body"]


