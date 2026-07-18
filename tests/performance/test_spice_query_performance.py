"""Performance test for the SPICE query API's latest-version resolution at scale."""

import datetime

import pytest

from sds_data_manager.lambda_code.SDSCode.api_lambdas import spice_query_api
from sds_data_manager.lambda_code.SDSCode.database.models import SPICEFiles

NUM_FILE_ROOTS = 2000
START_DATE = datetime.datetime(2024, 1, 1)


@pytest.fixture
def sample_data():
    """Build many SPICE file roots, each with 1-50 versions."""
    rows = []
    for root_num in range(NUM_FILE_ROOTS):
        day = START_DATE + datetime.timedelta(days=root_num)
        file_root = f"imap_{day:%Y_%j}_{day:%Y_%j}_"
        num_versions = (root_num * 37) % 50 + 1
        for version in range(num_versions):
            rows.append(
                {
                    "file_path": f"ck/{file_root}{version:03d}.ah.bc",
                    "file_name": f"{file_root}{version:03d}.ah.bc",
                    "file_root": f"{file_root}.ah.bc",
                    "kernel_type": "ck",
                    "version": version,
                    "ingestion_date": START_DATE,
                    "min_date_datetime": day,
                    "max_date_datetime": day,
                    "released": True,
                }
            )
    return rows


def test_latest_spice_query_performance(time_constrained_sqlite_session, sample_data):
    """Latest-version resolution over many SPICE file roots completes within budget."""
    session = time_constrained_sqlite_session
    session.bulk_insert_mappings(SPICEFiles, sample_data)
    session.commit()

    event = {"queryStringParameters": {"latest": "true"}}
    response = spice_query_api.lambda_handler(event=event, context={})

    assert response["statusCode"] == 200
