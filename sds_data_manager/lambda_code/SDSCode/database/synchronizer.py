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

from ..pipeline_lambdas import spice_indexer
from . import database as db
from . import models

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):  # noqa: PLR0915, PLR0912
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
    # These files in s3 are not indexed into Database tables
    ignore_keys = ["dependency/", "quicklook/"]
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        if "Contents" in page:
            # Add keys that are not in the ignore list
            s3_files_dict.update(
                {
                    obj["Key"]: obj["LastModified"]
                    for obj in page["Contents"]
                    if not any(ignore_key in obj["Key"] for ignore_key in ignore_keys)
                }
            )

    s3_files = set(s3_files_dict.keys())

    # Fetch database entries
    with db.Session() as session:
        with session.begin():
            search_results = []
            query_science = select(models.ScienceFiles.file_path)
            query_ancillary = select(models.AncillaryFiles.file_path)
            # Add SPICE related entries
            query_spice = select(models.SPICEFiles.file_path)
            query_sff = select(models.SmallForcesFile.file_path)
            query_spin = select(models.SpinFiles.file_path)
            query_repoint = select(models.RepointFiles.file_path)
            search_results.extend(session.execute(query_science).all())
            search_results.extend(session.execute(query_ancillary).all())
            search_results.extend(session.execute(query_spice).all())
            search_results.extend(session.execute(query_sff).all())
            search_results.extend(session.execute(query_spin).all())
            search_results.extend(session.execute(query_repoint).all())

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
        for filepath in s3_only_files:
            filename = filepath.split("/")[-1]
            if len(filename) < 2:
                # There are some directories as files I think? Ignore them for now.
                logger.warning("Ignoring invalid filename: %s", filename)
                continue

            imap_file = imap_data_access.file_validation.generate_imap_file_path(
                filename
            )

            # Determine file type
            is_science = isinstance(imap_file, imap_data_access.ScienceFilePath)
            is_ancillary = isinstance(imap_file, imap_data_access.AncillaryFilePath)
            is_spice = isinstance(imap_file, imap_data_access.SPICEFilePath)

            # Handle Science and Ancillary files
            if is_science or is_ancillary:
                file_params = imap_file.extract_filename_components(filename)

                # delete mission key from metadata params
                file_params.pop("mission")
                file_params["start_date"] = datetime.strptime(
                    file_params.pop("start_date"), "%Y%m%d"
                )
                # Check for end date
                if file_params.get("end_date", None):
                    file_params["end_date"] = datetime.strptime(
                        file_params.pop("end_date"), "%Y%m%d"
                    )
                file_params["file_path"] = filepath
                file_params["ingestion_date"] = s3_files_dict[filepath]
                # TODO: update to get release flag from S3 tags
                file_params["released"] = False

                if is_science:
                    record = models.ScienceFiles(**file_params)
                elif is_ancillary:
                    record = models.AncillaryFiles(**file_params)

                records_to_add.append(record)

            # Handle SPICE files
            elif is_spice:
                spice_type = imap_file.spice_metadata["type"]

                if spice_type == "repoint":
                    spice_indexer.index_pointing_data(filepath)
                    spice_indexer.index_repoint_file(filepath)
                elif spice_type == "spin":
                    logger.info(f"Indexing {filepath} spin table")
                    spice_indexer.index_spin_file(filepath)
                elif spice_type == "thruster":
                    logger.info(f"Indexing {filepath} small-forces table")
                    spice_indexer.index_small_forces_file(filepath)
                else:
                    # Write SPICE kernels to the SPICE table
                    spice_indexer.index_spice_file(filepath)

            # Unrecognized file type
            else:
                logger.warning("Unrecognized file type for file: %s", filename)

        session.add_all(records_to_add)

        # Remove database entries for files that were deleted from s3
        delete_science_files = delete(models.ScienceFiles).where(
            models.ScienceFiles.file_path.in_(db_only_files)
        )

        delete_ancillary_files = delete(models.AncillaryFiles).where(
            models.AncillaryFiles.file_path.in_(db_only_files)
        )

        delete_spice_files = delete(models.SPICEFiles).where(
            models.SPICEFiles.file_path.in_(db_only_files)
        )
        delete_small_forces_files = delete(models.SmallForcesFile).where(
            models.SmallForcesFile.file_path.in_(db_only_files)
        )
        delete_spin_files = delete(models.SpinFiles).where(
            models.SpinFiles.file_path.in_(db_only_files)
        )
        delete_repoint_files = delete(models.RepointFiles).where(
            models.RepointFiles.file_path.in_(db_only_files)
        )

        session.execute(delete_science_files)
        session.execute(delete_ancillary_files)
        session.execute(delete_spice_files)
        session.execute(delete_small_forces_files)
        session.execute(delete_spin_files)
        session.execute(delete_repoint_files)

        session.commit()
