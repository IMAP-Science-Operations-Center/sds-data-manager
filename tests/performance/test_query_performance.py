"""Performance test for the query API's latest-version resolution at scale."""

import datetime

import pytest

from sds_data_manager.lambda_code.SDSCode.api_lambdas import query_api
from sds_data_manager.lambda_code.SDSCode.database.models import ScienceFiles

DAYS = 365 * 2
START_DATE = datetime.datetime(2024, 1, 1)


@pytest.fixture
def sample_data():
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
                    "released": True,
                }
            )
    return rows


def test_latest_query_performance(time_constrained_sqlite_session, sample_data):
    """Latest-version resolution over ~2 years of data completes within budget."""
    session = time_constrained_sqlite_session
    session.bulk_insert_mappings(ScienceFiles, sample_data)
    session.commit()

    event = {"queryStringParameters": {"instrument": "hit", "latest": "true"}}
    response = query_api.lambda_handler(event=event, context={})

    assert response["statusCode"] == 200
