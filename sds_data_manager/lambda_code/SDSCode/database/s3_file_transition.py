"""Lambda to transition S3 files to new version."""

from . import database as db
from . import models


def lambda_handler(event, context):
    """Lambda handler for transitioning S3 files to new version.

    This lambda is used one-time to transition s3 files to new
    version. This lambda is used in dev and prod to test
    and apply the transition of s3 files in production.

    Lambda code defined here will help with making sure
    dev and prod has same setup when transitioning.
    """
    # First take on steps of transitioning S3 files to new version. TBD
    # 1. Able to connect to s3 bucket and list files.
    # 2. Able to download files from s3 bucket, either using s3 API or imap-data-access.
    # 3. Able to rename files to new version and validate the new version
    #    using imap-data-access.
    # 4. Store in tmp location. TBD on details
    # 5. Able to make query to DB and science table.
    # 6. Verify the new version files record matches with what's in science table.
    # 7. Once verified, switch to the new version files.
    with db.Session() as session:
        # Query science table
        session.query(models.ScienceFiles).all()
        pass

    return {
        "statusCode": 200,
        "body": "S3 file transition completed successfully.",
    }
