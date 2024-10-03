"""Synchronize database with S3 bucket.

This script compares the contents of an S3 bucket with a database table and
updates the database with any missing files or removes entries for deleted
files.
"""

import logging
import os
from datetime import datetime

import boto3
import imap_data_access
from sqlalchemy import delete, select

from . import database as db
from . import models

logger = logging.getLogger(__name__)


def lambda_handler(event, context):
    """Entry point to the database synchronizer lambda.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    """
    logger.info("Synchronizing database with S3 bucket")

    # S3 and database configuration
    client = boto3.client("s3")
    bucket = os.getenv("S3_BUCKET")
    # Paginate through S3 objects (needed because we likely have more than 1000 items)
    # TODO: Do we want to limit the scope of these query comparisons?
    #       We may run into performance issues if we have a large number of files.
    #       Could put an outer loop over instrument + level if needed.
    paginator = client.get_paginator("list_objects_v2")
    prefix = "imap/"
    s3_files_dict = {}
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        if "Contents" in page:
            s3_files_dict.update(
                {obj["Key"]: obj["LastModified"] for obj in page["Contents"]}
            )

    s3_files = set(s3_files_dict.keys())

    # Fetch database entries
    with db.Session() as session:
        with session.begin():
            query = select(models.ScienceFiles.file_path)
            search_results = session.execute(query).all()

        # result is a one-element tuple, so we need to extract the filepath
        db_files = set([result[0] for result in search_results])

        # Find discrepancies
        s3_only_files = set(s3_files) - db_files
        db_only_files = db_files - set(s3_files)

        if len(s3_files) == 0 and len(db_files) == 0:
            logger.info("No conflicting files found")
            return

        logger.info("Conflicting files found, syncing up the DB to match s3")
        logger.info(
            "S3 only files to be added [%d]: %s", len(s3_only_files), s3_only_files
        )
        logger.info(
            "DB only files to be removed [%d]: %s", len(db_only_files), db_only_files
        )

        # Update database with missing S3 files
        records_to_add = []
        for filename in s3_only_files:
            file_params = imap_data_access.ScienceFilePath.extract_filename_components(
                filename.split("/")[-1]
            )

            # delete mission key from metadata params
            file_params.pop("mission")
            file_params["start_date"] = datetime.strptime(
                file_params.pop("start_date"), "%Y%m%d"
            )

            file_params["file_path"] = filename
            file_params["ingestion_date"] = s3_files_dict[filename]
            records_to_add.append(models.ScienceFiles(**file_params))
        session.add_all(records_to_add)

        # Remove database entries for files that were deleted from s3
        delete_statement = delete(models.ScienceFiles).where(
            models.ScienceFiles.file_path.in_(db_only_files)
        )

        session.execute(delete_statement)
        session.commit()
