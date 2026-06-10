"""Lambda function for release API endpoint."""

import datetime
import json
import logging
from pathlib import Path

import imap_data_access
from imap_data_access.file_validation import (
    AncillaryFilePath,
    ScienceFilePath,
    generate_imap_file_path,
)
from sqlalchemy import func, or_, text, union_all

from ..database import database as db
from ..database import models
from ..spice_utilities import download_from_s3

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


def query_releasable_science_files(
    session,
    instrument,
    start_date,
    end_date,
    release_number,
    science_files_to_exclude=None,
):
    """Query releaseable science files for given release number.

    At every mission wide release, all data that will be released
    to public should have vRRR.000. Eg. If release 1, all data
    since launch will now have v001.000. If release 2,
    all data will have v002.000 and so on.

    In between public release version, version MMM will be used to
    track updates to intermediate data. eg. After public
    release 1, data can have v001.001 or v001.012 and so on
    indicating that data was reprocessed 1 or 12 times respectively
    since release 1, due to updates, such as input data changed
    or processing code changed.
    """
    science_table = models.ScienceFiles

    # query for release number for the given instrument and date range
    latest_science_files = session.query(science_table).filter(
        science_table.instrument == instrument,
        science_table.start_date >= start_date,
        science_table.start_date <= end_date,
        science_table.release_number == release_number,
        science_table.data_version == 0,
    )

    if science_files_to_exclude:
        latest_science_files = latest_science_files.filter(
            ~science_table.file_path.in_(science_files_to_exclude)
        )
    results = list(latest_science_files.all())
    logger.info(f"Found {len(results)} science file(s) for instrument={instrument}")
    return results


def get_latest_ancillary_files(
    session,
    instrument: str,
    start_date: datetime.datetime,
    end_date: datetime.datetime,
    ancillary_files_to_exclude: list | None = None,
) -> list:
    """Get latest-version ancillary files for an instrument over a date range.

    The function retrieves files in two groups based on overlap with date range:

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
    ancillary_files_to_exclude : list, optional
        List of ancillary file paths to exclude from results, by default None

    Returns
    -------
    list
        List of file paths ordered by file_path.
    """
    ancillary_table = models.AncillaryFiles

    # Step 1: Get latest version per (descriptor, start_date, end_date)
    # Filter by instrument early to reduce data processed
    row_num_col = (
        func.row_number()
        .over(
            partition_by=[
                ancillary_table.descriptor,
                ancillary_table.start_date,
                ancillary_table.end_date,
            ],
            order_by=ancillary_table.version.desc(),
        )
        .label("row_num")
    )

    latest_versions = (
        session.query(
            ancillary_table.file_path,
            ancillary_table.descriptor,
            ancillary_table.start_date,
            ancillary_table.end_date,
        )
        .filter(ancillary_table.instrument == instrument)
        .add_columns(row_num_col)
        .subquery()
    )

    latest = (
        session.query(
            latest_versions.c.file_path,
            latest_versions.c.descriptor,
            latest_versions.c.start_date,
            latest_versions.c.end_date,
        )
        .filter(latest_versions.c.row_num == 1)
        .subquery()
    )

    # Step 2: Files WITH end_date - simple overlap check
    with_end_date_query = session.query(latest.c.file_path).filter(
        latest.c.end_date.isnot(None),
        latest.c.start_date <= end_date,
        latest.c.end_date >= start_date,
    )

    # Step 3: Files WITHOUT end_date - use LEAD() to find coverage end
    next_start_col = (
        func.lead(latest.c.start_date)
        .over(partition_by=latest.c.descriptor, order_by=latest.c.start_date)
        .label("next_start_date")
    )

    no_end_with_next = (
        session.query(latest.c.file_path, latest.c.start_date, next_start_col)
        .filter(latest.c.end_date.is_(None))
        .subquery()
    )

    no_end_date_query = session.query(no_end_with_next.c.file_path).filter(
        no_end_with_next.c.start_date <= end_date,
        or_(
            no_end_with_next.c.next_start_date.is_(None),
            no_end_with_next.c.next_start_date > start_date,
        ),
    )

    # Combine — ORDER BY positional index works across all DB backends for UNION ALL
    combined = union_all(with_end_date_query, no_end_date_query).order_by(text("1"))

    latest_ancillary_files = [row[0] for row in session.execute(combined).fetchall()]

    # Now exclude any files in the exclude list
    if ancillary_files_to_exclude:
        latest_ancillary_files = [
            p for p in latest_ancillary_files if p not in ancillary_files_to_exclude
        ]

    if not latest_ancillary_files:
        logger.info(f"Found 0 ancillary file(s) for instrument={instrument}")
        return []

    results = list(
        session.query(models.AncillaryFiles).filter(
            models.AncillaryFiles.file_path.in_(latest_ancillary_files)
        )
    )
    logger.info(f"Found {len(results)} ancillary file(s) for instrument={instrument}")
    return results


def download_read_file(exception_list_file_path):
    """Download a manifest file from S3 and group its entries by file type.

    Parameters
    ----------
    exception_list_file_path : str
        S3 path to the manifest text file. Each line is an IMAP file path.

    Returns
    -------
    tuple[list[str], list[str]]
        A tuple of (science_files, ancillary_files) where each entry is the
        file path string listed in the manifest.
    """
    # Create the proper file path object based on the extension and filename
    file_path = Path(exception_list_file_path)
    path_obj = generate_imap_file_path(file_path.name)

    s3_file_path = (
        path_obj.construct_path()
        .relative_to(imap_data_access.config["DATA_DIR"])
        .as_posix()
    )

    logger.debug(f"Downloading manifest file from S3 path: {s3_file_path}")
    download_path = download_from_s3(s3_file_path)
    logger.debug(f"Local path after download: {download_path}")
    lines = download_path.read_text(encoding="utf-8").splitlines()

    science_files = []
    ancillary_files = []
    for line in lines:
        filename = line.strip()
        if not filename:
            continue
        file_obj = imap_data_access.file_validation.generate_imap_file_path(filename)
        if isinstance(file_obj, ScienceFilePath):
            science_files.append(filename)
        elif isinstance(file_obj, AncillaryFilePath):
            ancillary_files.append(filename)
        else:
            logger.warning(f"Unrecognized file type in manifest, skipping: {filename}")

    return science_files, ancillary_files


def release_type_handler(query_params):
    """Handle 'release' type requests."""
    start_date = datetime.datetime.strptime(query_params["start_date"], "%Y%m%d")
    end_date = datetime.datetime.strptime(query_params["end_date"], "%Y%m%d")
    exclude_file = query_params.get("exclude_file", None)
    release_number = int(query_params["release_number"])
    instrument = query_params["instrument"]

    with db.Session() as session:
        science_files_to_exclude = []
        ancillary_files_to_exclude = []

        if exclude_file:
            science_files_to_exclude, ancillary_files_to_exclude = download_read_file(
                exclude_file
            )

        science_files_to_update = query_releasable_science_files(
            session,
            instrument,
            start_date,
            end_date,
            release_number,
            science_files_to_exclude=science_files_to_exclude,
        )

        ancillary_files_to_update = get_latest_ancillary_files(
            session,
            instrument,
            start_date,
            end_date,
            ancillary_files_to_exclude=ancillary_files_to_exclude,
        )

        if not science_files_to_update and not ancillary_files_to_update:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    "No files to release for the specified "
                    f"{instrument}, {start_date} to {end_date}, and "
                    f"{release_number}."
                ),
            }

        for obj in science_files_to_update:
            obj.released = True

        for obj in ancillary_files_to_update:
            obj.released = True

        session.commit()

        return {
            "statusCode": 200,
            "body": json.dumps(
                f"Successfully released "
                f"{len(science_files_to_update)} science files and "
                f"{len(ancillary_files_to_update)} ancillary files."
            ),
        }


def early_release_type_handler(query_params):
    """Handle early-release requests using manifest file."""
    manifest_file = query_params["manifest_file"]

    science_files, ancillary_files = download_read_file(manifest_file)

    if not science_files and not ancillary_files:
        return {
            "statusCode": 400,
            "body": json.dumps("No files found in the manifest file."),
        }

    with db.Session() as session:
        if science_files:
            session.query(models.ScienceFiles).filter(
                models.ScienceFiles.file_path.in_(science_files)
            ).update(
                {models.ScienceFiles.released: True},
                synchronize_session=False,
            )

        if ancillary_files:
            session.query(models.AncillaryFiles).filter(
                models.AncillaryFiles.file_path.in_(ancillary_files)
            ).update(
                {models.AncillaryFiles.released: True},
                synchronize_session=False,
            )

        session.commit()

    logger.info(
        f"Early released {len(science_files)} science files and "
        f"{len(ancillary_files)} ancillary files."
    )

    return {
        "statusCode": 200,
        "body": json.dumps(
            f"Successfully early released "
            f"{len(science_files)} science files and "
            f"{len(ancillary_files)} ancillary files."
        ),
    }


def unrelease_type_handler(query_params):
    """Handle unrelease requests using manifest file."""
    manifest_file = query_params["manifest_file"]

    science_files, ancillary_files = download_read_file(manifest_file)

    if not science_files and not ancillary_files:
        return {
            "statusCode": 400,
            "body": json.dumps(
                "The manifest file does not contain any science or ancillary files."
            ),
        }

    with db.Session() as session:
        if science_files:
            session.query(models.ScienceFiles).filter(
                models.ScienceFiles.file_path.in_(science_files)
            ).update(
                {models.ScienceFiles.released: False},
                synchronize_session=False,
            )

        if ancillary_files:
            session.query(models.AncillaryFiles).filter(
                models.AncillaryFiles.file_path.in_(ancillary_files)
            ).update(
                {models.AncillaryFiles.released: False},
                synchronize_session=False,
            )

        session.commit()

    logger.info(
        f"Unreleased {len(science_files)} science files and "
        f"{len(ancillary_files)} ancillary files."
    )

    return {
        "statusCode": 200,
        "body": json.dumps(
            f"Successfully unreleased "
            f"{len(science_files)} science files and "
            f"{len(ancillary_files)} ancillary files."
        ),
    }


def reprocess_type_handler(query_params):
    """Update global release table with the new release number.

    This table update is monitored by Dagster sensor. When this table is
    updated, it will trigger reprocessing for the new release number.
    """
    release_number = int(query_params["release_number"])

    with db.Session() as session:
        # First check that there is no existing DB record with
        # release number.
        existing_record = (
            session.query(models.GlobalRelease)
            .filter(models.GlobalRelease.release_number == release_number)
            .first()
        )

        if existing_record:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    f"Release number {release_number} already exists. "
                    "Please choose a release number that has not been used before."
                ),
            }

        # If new release number is not increment by 1 from the latest release number.
        latest_release = (
            session.query(models.GlobalRelease)
            .order_by(models.GlobalRelease.release_number.desc())
            .first()
        )

        if latest_release and release_number != latest_release.release_number + 1:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    f"Release number {release_number} is not the next increment "
                    f"from the latest release number "
                    f"{latest_release.release_number}. "
                    "Please choose the next sequential release number."
                ),
            }

        # Add to table with the new release number and update the timestamp.
        new_release = models.GlobalRelease(
            release_number=release_number,
            updated_date=datetime.datetime.utcnow(),
        )

        session.add(new_release)
        session.commit()

        return {
            "statusCode": 200,
            "body": json.dumps(
                f"Successfully created release number {release_number}. "
                "Dagster reprocessing will be triggered automatically."
            ),
        }


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
