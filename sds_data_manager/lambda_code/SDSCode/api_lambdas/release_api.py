"""Lambda function for release API endpoint."""

import datetime
import json
import logging

import imap_data_access
from imap_data_access.file_validation import (
    generate_imap_file_path,
)
from sqlalchemy import and_, func, literal, or_, select

from ..database import database as db
from ..database import models
from ..spice_utilities import download_from_s3
from .utils import build_latest_version_query

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def check_api_key(event):
    """Check API key scope; only non-read keys may release files."""
    request_ctx = event.get("requestContext", {})
    auth = request_ctx.get("authorizer", {})
    auth_ctx = auth.get("lambda", {})
    scope = auth_ctx.get("scope", "")
    api_key = auth_ctx.get("apiKey", "unknown")

    logger.info(f"Release request received with scope: {scope}, api_key: {api_key}")

    if scope == "read":
        logger.warning("Release denied: read scope user attempted release operation")
        return {
            "statusCode": 403,
            "body": json.dumps(
                "Release operation denied. Your API key has read permissions."
            ),
        }

    return {
        "statusCode": 200,
        "body": json.dumps("API key validated successfully."),
    }


def validate_query_params(event):
    """Validate query parameters and return (is_valid, error_message)."""
    query_params = event.get("queryStringParameters") or {}

    # Validate release_type and derive the released flag value.
    release_type = query_params["release_type"]
    valid_release_types = ["release", "unrelease", "early-release"]
    if release_type not in valid_release_types:
        return {
            "statusCode": 400,
            "body": json.dumps(
                f"'{release_type}' is not a valid release_type. "
                f"Valid options are: {valid_release_types}"
            ),
        }

    if release_type == "release" and "release_number" not in query_params:
        return {
            "statusCode": 400,
            "body": json.dumps(
                "'release_number' query parameter is required when "
                "'release_type' is 'release'. Please provide a release_number "
                "indicating which release batch to apply. For example, "
                "withhold files with 'release_number=1' will be included in "
                "the first release batch, 'release_number=2' in the second, "
                "and so on."
            ),
        }

    valid_parameters = [
        "instrument",
        "start_date",
        "end_date",
        "release_type",
        "exclude_file",
        "manifest_file",
        "release_number",
    ]

    for param in query_params:
        if param not in valid_parameters:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    f"'{param}' is not a valid query parameter. "
                    f"Valid query parameters are: {valid_parameters}"
                ),
            }

    return {
        "statusCode": 200,
        "data": {
            "release_type": release_type,
            "query_params": query_params,
        },
    }


def latest_ancillary_release(
    session,
    start_date: datetime.datetime,
    end_date: datetime.datetime,
    line: str,
):
    """Set the released flag to True for latest-version ancillary files.

    The function selects files in two groups based on overlap with date range:

        Files with explicit end_date in their filename:
            overlaps if start_date <= query_end AND end_date >= query_start

        Files without end_date in their filename and considered valid until
        the next file with start_date after it appears:
            overlaps if start_date <= query_end AND
            (next_file_start >= query_start OR no next file)

    Parameters
    ----------
    session : orm session
        Database session.
    instrument : str
        Instrument name.
    start_date : datetime.datetime
        Start of query date range.
    end_date : datetime.datetime
        End of query date range.
    line : str
        Manifest line describing the ancillary release selection.

    Returns
    -------
    Query
        A query of a single file_path column, for use as an `.in_()`
        subquery so a large release stays a single UPDATE statement.
    """
    ancillary_table = models.AncillaryFiles
    instrument, data_type, descriptor, _ = (item.strip() for item in line.split(","))
    # Scenarios:
    # hit, *, *, true, -- release all ancillary files
    # hit, ancillary, *, true -- release all ancillary descriptors
    # hit, ancillary, x-descriptor, true - release only specified
    if data_type == "*" or descriptor == "*":
        filters = [ancillary_table.instrument == instrument]
    else:
        filters = [
            ancillary_table.instrument == instrument,
            ancillary_table.descriptor == descriptor,
        ]

    #
    # Filter first so window functions operate on a small dataset.
    #
    filtered = select(ancillary_table).where(*filters).subquery()

    #
    # RANK() to get latest version.
    #
    version_rank = (
        func.rank()
        .over(
            partition_by=[
                filtered.c.instrument,
                filtered.c.descriptor,
                filtered.c.start_date,
                filtered.c.end_date,
            ],
            order_by=filtered.c.version.desc(),
        )
        .label("version_rank")
    )

    # LEAD() to set start_date as end_date for files that didn't
    # end_date
    next_start_date = (
        func.lead(filtered.c.start_date)
        .over(
            partition_by=[
                filtered.c.instrument,
                filtered.c.descriptor,
            ],
            order_by=filtered.c.start_date,
        )
        .label("next_start_date")
    )

    windowed = select(
        filtered,
        version_rank,
        next_start_date,
    ).subquery()

    #
    # Keep only the latest major version.
    #
    latest = select(windowed).where(windowed.c.version_rank == 1).subquery()

    #
    # Use end_date when present; otherwise use the next file's
    # start_date. If there is no next file, treat it as open-ended.
    #
    next_end = func.coalesce(latest.c.next_start_date, literal(datetime.datetime.max))

    overlap_condition = or_(
        and_(latest.c.end_date.is_not(None), latest.c.end_date >= start_date),
        and_(latest.c.end_date.is_(None), next_end > start_date),
    )

    latest_file_paths = select(latest.c.file_path).where(
        latest.c.start_date <= end_date,
        overlap_condition,
    )
    latest_records = session.query(models.AncillaryFiles).filter(
        models.AncillaryFiles.file_path.in_(latest_file_paths)
    )
    release_rows = latest_records.all()

    latest_records.update(
        {models.AncillaryFiles.released: True},
        synchronize_session=False,
    )
    return release_rows


def latest_science_release(session, start_date, end_date, line):
    """Set the released flag to True for latest-version science files."""
    sci = models.ScienceFiles.__table__.c
    instrument, data_type, descriptor, _ = (item.strip() for item in line.split(","))

    # Construct query logic based on different scenarios:
    # 1. hit, *, *, true -- release all data levels
    if data_type == "*":
        query = [
            sci.instrument == instrument,
            sci.start_date >= start_date,
            sci.start_date <= end_date,
        ]
    # 2. hit, l1a, *, true -- release all descriptor for given level
    elif descriptor == "*":
        query = [
            sci.instrument == instrument,
            sci.data_level == data_type,
            sci.start_date >= start_date,
            sci.start_date <= end_date,
        ]
    # 3. release specified level and descriptor
    else:
        query = [
            sci.instrument == instrument,
            sci.data_level == data_type,
            sci.descriptor == descriptor,
            sci.start_date >= start_date,
            sci.start_date <= end_date,
        ]

    latest = build_latest_version_query(
        filters=query,
    )
    latest_file_paths = latest.with_only_columns(latest.selected_columns.file_path)
    latest_records = session.query(models.ScienceFiles).filter(
        models.ScienceFiles.file_path.in_(latest_file_paths)
    )
    release_rows = latest_records.all()
    # Finally update released flag to True
    latest_records.update(
        {models.ScienceFiles.released: True},
        synchronize_session=False,
    )
    return release_rows


def download_file(manifest_file):
    """Download a manifest file from S3.

    Parameters
    ----------
    manifest_file : str
        S3 path to the manifest text file. Each line is an IMAP file path.

    Returns
    -------
    Path
        Download file path
    """
    # Create the proper file path object based on the extension and filename
    path_obj = generate_imap_file_path(manifest_file)

    s3_file_path = (
        path_obj.construct_path()
        .relative_to(imap_data_access.config["DATA_DIR"])
        .as_posix()
    )

    logger.debug(f"Downloading manifest file from S3 path: {s3_file_path}")
    download_path = download_from_s3(s3_file_path)
    logger.debug(f"Local path after download: {download_path}")
    return download_path


def release_type_handler(query_params):
    """Handle 'release' type requests."""
    manifest_file = query_params.get("manifest_file", None)

    if manifest_file is None:
        return {
            "statusCode": 400,
            "body": json.dumps(
                "Missing required query parameter 'manifest_file' "
                "for release operation."
            ),
        }

    with db.Session() as session:
        manifest_path = download_file(manifest_file)

        manifest_file_obj = generate_imap_file_path(manifest_path.name)
        start_date = datetime.datetime.strptime(manifest_file_obj.start_date, "%Y%m%d")
        end_date = datetime.datetime.strptime(manifest_file_obj.end_date, "%Y%m%d")

        # Read all lines in manifest file. We don't use numpy or pandas here
        # because library will make lambda layer exceed its size limit.
        all_lines = manifest_path.read_text(encoding="utf-8").splitlines()
        for line in all_lines:
            # Skip comment or empty lines
            if line.startswith("#") or line.strip() == "":
                continue
            # Skip Header
            if line.startswith("instrument,"):
                continue

            _, data_type, _, release_flag = (item.strip() for item in line.split(","))
            release_flag = release_flag == "true"
            # If row is to exclude, skip release process.
            if not release_flag:
                continue

            if data_type == "*":
                # Release all data for given instrument for the date
                # range, including both ancillary and all data levels.
                # Eg. hit, *, *, true
                latest_science_release(
                    session=session, start_date=start_date, end_date=end_date, line=line
                )
                latest_ancillary_release(session, start_date, end_date, line)
            elif data_type == "ancillary":
                latest_ancillary_release(session, start_date, end_date, line)
            else:
                logger.info(f"Releasing science files for line: {line}")
                latest_science_release(
                    session=session, start_date=start_date, end_date=end_date, line=line
                )

        session.commit()

        return {
            "statusCode": 200,
            "body": json.dumps("Successfully released "),
        }


def early_release_type_handler(query_params):
    """Handle early-release requests using manifest file."""
    return {"statusCode": 200, "body": "Early release operation not supported yet."}


def unrelease_type_handler(query_params):
    """Handle unrelease requests using manifest file."""
    return {"statusCode": 200, "body": "Unrelease operation not supported yet."}


def reprocess_type_handler(query_params):
    """Reprocess for data release.

    NOTE: This may not be needed. If not needed, remove support
    at imap-data-access before removing this.
    """
    return {"statusCode": 200, "body": "Reprocess for data release not supported yet."}


def lambda_handler(event, context):
    """Entry point for the release API lambda.

    Required query parameters for 'release' type:
        instrument   : instrument name (e.g. mag, swe, lo, codice)
        start_date   : inclusive lower bound on file start_date (YYYYMMDD)
        end_date     : inclusive upper bound on file start_date (YYYYMMDD)

    Optional parameters for 'release' type:
        exclude_file : S3 path to manifest text file listing files to
                       exclude from release. Each line in the manifest
                       can contain science or ancillary filename.

    Required parameters for 'early' or 'unrelease' type:
        manifest_file : S3 path to manifest text file listing files to
        release/unrelease.

    Parameters
    ----------
    event : dict
        Input event containing ``queryStringParameters``.
    context : LambdaContext
        Lambda runtime context object.
    """
    logger.info("Received release request with event: " + json.dumps(event, indent=2))

    # Check API key and scope. Only API keys with non-read scopes may release files.
    api_key_check = check_api_key(event)
    if api_key_check["statusCode"] != 200:
        return api_key_check

    # Validate query parameters.
    query_validation = validate_query_params(event)
    if query_validation["statusCode"] != 200:
        return query_validation

    release_type = query_validation["data"]["release_type"]
    query_params = query_validation["data"]["query_params"]

    handler_map = {
        "release": release_type_handler,
        "early-release": early_release_type_handler,
        "unrelease": unrelease_type_handler,
        "reprocess": reprocess_type_handler,
    }

    handler = handler_map.get(release_type)

    if handler is None:
        return {
            "statusCode": 400,
            "body": json.dumps(f"Invalid release type: {release_type}"),
        }

    return handler(query_params)
