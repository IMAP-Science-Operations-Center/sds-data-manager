"""Performance test for the release API's latest-version resolution at scale."""

import datetime

import pytest

from sds_data_manager.lambda_code.SDSCode.api_lambdas import release_api
from sds_data_manager.lambda_code.SDSCode.database.models import ScienceFiles

DAYS = 365 * 2
START_DATE = datetime.datetime(2024, 1, 1)


@pytest.fixture
def unreleased_data():
    """Build ~2 years of daily science files, each with 1-100 minor versions."""
    rows = []
    for day in range(DAYS):
        start_date = START_DATE + datetime.timedelta(days=day)
        num_versions = (day * 37) % 100 + 1
        for minor_version in range(1, num_versions + 1):
            rows.append(
                {
                    "file_path": (
                        f"imap_hit_l1a_count_{start_date:%Y%m%d}_"
                        f"v001.{minor_version:04d}.cdf"
                    ),
                    "instrument": "hit",
                    "data_level": "l1a",
                    "descriptor": "count",
                    "start_date": start_date,
                    "major_version": 1,
                    "minor_version": minor_version,
                    "extension": "cdf",
                    "ingestion_date": start_date,
                    "released": False,
                }
            )
    return rows


def _event(params):
    """Build a release API Gateway event with a non-read API key scope."""
    return {
        "queryStringParameters": params,
        "requestContext": {
            "authorizer": {"lambda": {"apiKey": "test-key", "scope": "full"}}
        },
    }


def test_release_query_performance(time_constrained_sqlite_session, unreleased_data):
    """Releasing latest versions over ~2 years of data completes within budget."""
    session = time_constrained_sqlite_session
    session.bulk_insert_mappings(ScienceFiles, unreleased_data)
    session.commit()

    params = {
        "instrument": "hit",
        "start_date": "20000101",
        "end_date": "20991231",
        "release_type": "release",
        "release_number": "1",
    }
    response = release_api.lambda_handler(event=_event(params), context={})

    assert response["statusCode"] == 200
