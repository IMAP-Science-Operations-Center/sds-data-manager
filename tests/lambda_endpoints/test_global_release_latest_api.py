"""Unit tests for the global release latest API."""

import datetime
import json

from sds_data_manager.lambda_code.SDSCode.api_lambdas import global_release_latest_api
from sds_data_manager.lambda_code.SDSCode.database.models import GlobalRelease


def test_global_release_latest_returns_latest_release_number(session):
    """Return the row with the highest release number."""
    session.add_all(
        [
            GlobalRelease(
                release_number=5,
                updated_date=datetime.datetime(2026, 5, 1, 10, 0, 0),
            ),
            GlobalRelease(
                release_number=6,
                updated_date=datetime.datetime(2026, 5, 2, 10, 0, 0),
            ),
        ]
    )
    session.commit()

    response = global_release_latest_api.lambda_handler(
        event={"rawPath": "/global-release/latest", "queryStringParameters": {}},
        context={},
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["release_number"] == 6
    assert body["updated_date"] == "2026-05-02T10:00:00"


def test_global_release_latest_returns_404_when_no_data(session):
    """Return 404 if no global release row exists yet."""
    response = global_release_latest_api.lambda_handler(
        event={"rawPath": "/global-release/latest", "queryStringParameters": {}},
        context={},
    )

    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert body["error"] == "No global release number found."
