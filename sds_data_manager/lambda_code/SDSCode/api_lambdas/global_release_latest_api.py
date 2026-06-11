"""Lambda function for GET /global-release/latest."""

import json
import logging

from sqlalchemy import select

from ..database import database as db
from ..database.models import GlobalRelease

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Return the latest global release number.

    The latest row is selected by descending ``release_number``.
    """
    logger.info("Received event: %s", json.dumps(event, indent=2))

    with db.Session() as session:
        latest_release = session.scalars(
            select(GlobalRelease).order_by(GlobalRelease.release_number.desc()).limit(1)
        ).first()

    if latest_release is None:
        return {
            "statusCode": 404,
            "body": json.dumps({"error": "No global release number found."}),
        }

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "release_number": latest_release.release_number,
                "updated_date": latest_release.updated_date.isoformat(),
            }
        ),
    }
