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
from sqlalchemy import func, or_, select, union_all

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

    # "unrelease" sets released=False; everything else sets released=True.
    released_flag = release_type != "unrelease"

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
            "released_flag": released_flag,
            "query_params": query_params,
        },
    }


def query_withhold_files(session, instrument, start_date, end_date, release_number):
    """Query for the latest-version withhold files matching given criteria."""
    descriptor = f"withhold-data-release-{int(release_number):03d}"
    release_table = models.ReleaseFiles

    # Query latest withhold file versions for given group
    # ( instrument, descriptor, and date range).
    max_ver_subq = (
        session.query(
            release_table.instrument,
            release_table.descriptor,
            release_table.start_date,
            release_table.end_date,
            func.max(release_table.version).label("max_version"),
        )
        .group_by(
            release_table.instrument,
            release_table.descriptor,
            release_table.start_date,
            release_table.end_date,
        )
        .subquery()
    )
    # Now query for specific withhold file matching input
    withhold_files = (
        session.query(release_table)
        .join(
            max_ver_subq,
            (release_table.instrument == max_ver_subq.c.instrument)
            & (release_table.descriptor == max_ver_subq.c.descriptor)
            & (release_table.start_date == max_ver_subq.c.start_date)
            & (release_table.end_date == max_ver_subq.c.end_date)
            & (release_table.version == max_ver_subq.c.max_version),
        )
        .filter(
            release_table.instrument == instrument,
            release_table.end_date >= start_date,
            release_table.start_date <= end_date,
            release_table.descriptor == descriptor,
        )
        .one_or_none()  # TODO: update this logic if we want to
        # support more than one withhold file per release batch
    )
    return withhold_files


def query_latest_science_files(session, instrument, start_date, end_date):
    """Query for the latest-version science file paths matching given criteria."""
    science_table = models.ScienceFiles

    max_ver_subq = (
        session.query(
            science_table.instrument,
            science_table.data_level,
            science_table.descriptor,
            science_table.start_date,
            func.max(science_table.version).label("max_version"),
        )
        .group_by(
            science_table.instrument,
            science_table.data_level,
            science_table.descriptor,
            science_table.start_date,
        )
        .subquery()
    )
    latest_science_files = [
        row.file_path
        for row in (
            session.query(science_table.file_path)
            .join(
                max_ver_subq,
                (science_table.instrument == max_ver_subq.c.instrument)
                & (science_table.descriptor == max_ver_subq.c.descriptor)
                & (science_table.start_date == max_ver_subq.c.start_date)
                & (science_table.version == max_ver_subq.c.max_version),
            )
            .filter(
                science_table.instrument == instrument,
                science_table.start_date >= start_date,
                science_table.start_date <= end_date,
            )
            .all()
        )
    ]
    logger.info(
        f"Found {len(latest_science_files)} science file(s) for instrument={instrument}"
    )
    return latest_science_files


def get_latest_ancillary_files(
    session,
    instrument: str,
    start_date: datetime.datetime,
    end_date: datetime.datetime,
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

    Returns
    -------
    list
        List of file paths ordered by file_path.
    """
    ancillary_table = models.AncillaryFiles

    # ========================================
    # Step 1: Keep only latest version per
    # (instrument, descriptor, start_date, end_date).
    # ========================================
    row_num_col = (
        func.row_number()
        .over(
            partition_by=[
                ancillary_table.instrument,
                ancillary_table.descriptor,
                ancillary_table.start_date,
                ancillary_table.end_date,
            ],
            order_by=ancillary_table.version.desc(),
        )
        .label("row_num")
    )
    ranked = session.query(
        ancillary_table.file_path,
        ancillary_table.instrument,
        ancillary_table.descriptor,
        ancillary_table.start_date,
        ancillary_table.end_date,
        ancillary_table.version,
        row_num_col,
    ).subquery()

    latest_versions = session.query(ranked).filter(ranked.c.row_num == 1).subquery()

    # ========================================
    # Step 2: Files with end_date
    # ========================================
    with_end_date_query = session.query(
        latest_versions.c.file_path,
        latest_versions.c.instrument,
        latest_versions.c.descriptor,
        latest_versions.c.start_date,
        latest_versions.c.end_date,
        latest_versions.c.version,
    ).filter(
        latest_versions.c.instrument == instrument,
        latest_versions.c.end_date.isnot(None),
        latest_versions.c.start_date <= end_date,
        latest_versions.c.end_date >= start_date,
    )

    # ========================================
    # Step 3: Files without end_date
    # ========================================
    next_start_date_col = (
        func.lead(latest_versions.c.start_date)
        .over(
            partition_by=[
                latest_versions.c.instrument,
                latest_versions.c.descriptor,
            ],
            order_by=latest_versions.c.start_date,
        )
        .label("next_start_date")
    )
    no_end_date_coverage = (
        session.query(
            latest_versions.c.file_path,
            latest_versions.c.instrument,
            latest_versions.c.descriptor,
            latest_versions.c.start_date,
            latest_versions.c.end_date,
            latest_versions.c.version,
            next_start_date_col,
        )
        .filter(
            latest_versions.c.end_date.is_(None),
        )
        .subquery()
    )

    # ========================================================
    # Step 4: Look for files where start_date <= query_end AND
    # (next_file_start >= query_start)
    # ========================================================
    no_end_date_query = session.query(
        no_end_date_coverage.c.file_path,
        no_end_date_coverage.c.instrument,
        no_end_date_coverage.c.descriptor,
        no_end_date_coverage.c.start_date,
        no_end_date_coverage.c.end_date,
        no_end_date_coverage.c.version,
    ).filter(
        no_end_date_coverage.c.instrument == instrument,
        no_end_date_coverage.c.start_date <= end_date,
        or_(
            no_end_date_coverage.c.next_start_date.is_(None),
            no_end_date_coverage.c.next_start_date > start_date,
        ),
    )

    # ========================================
    # Final: UNION ALL and order by file_path.
    # ========================================
    combined = union_all(
        select(with_end_date_query.subquery()),
        select(no_end_date_query.subquery()),
    ).order_by("file_path")

    file_paths = [row.file_path for row in session.execute(combined).fetchall()]
    logger.info(
        f"Found {len(file_paths)} ancillary file(s) for instrument={instrument}"
    )
    return file_paths


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
    logger.debug(f"Download path after download: {download_path}")
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


def release_type_handler(released_flag, query_params):
    """Handle 'release' type requests."""
    start_date = datetime.datetime.strptime(query_params["start_date"], "%Y%m%d")
    end_date = datetime.datetime.strptime(query_params["end_date"], "%Y%m%d")

    with db.Session() as session:
        # Query for withhold files to exclude from release.
        science_files_to_exclude = []
        ancillary_files_to_exclude = []

        withhold_files = query_withhold_files(
            session,
            query_params["instrument"],
            start_date,
            end_date,
            query_params.get("release_number"),
        )
        if withhold_files:
            science_files_to_exclude, ancillary_files_to_exclude = download_read_file(
                withhold_files.file_path
            )

        science_files_to_update = query_latest_science_files(
            session,
            query_params["instrument"],
            start_date,
            end_date,
        )
        if science_files_to_exclude != []:
            science_files_to_update = [
                file_path
                for file_path in science_files_to_update
                if Path(file_path).name not in science_files_to_exclude
            ]

        ancillary_files_to_update = get_latest_ancillary_files(
            session,
            query_params["instrument"],
            start_date,
            end_date,
        )
        if ancillary_files_to_exclude != []:
            ancillary_files_to_update = [
                file_path
                for file_path in ancillary_files_to_update
                if Path(file_path).name not in ancillary_files_to_exclude
            ]

        # For all science and ancillary files, update released flag for all
        # applicable files.
        session.query(models.ScienceFiles).filter(
            models.ScienceFiles.file_path.in_(science_files_to_update)
        ).update(
            {models.ScienceFiles.released: released_flag}, synchronize_session=False
        )

        session.query(models.AncillaryFiles).filter(
            models.AncillaryFiles.file_path.in_(ancillary_files_to_update)
        ).update(
            {models.AncillaryFiles.released: released_flag}, synchronize_session=False
        )

        session.commit()


def early_release_type_handler(query_params):
    """Handle early-release requests using manifest file."""
    manifest_file = query_params["manifest_file"]

    science_files, ancillary_files = download_read_file(manifest_file)

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
        f"Early released "
        f"{len(science_files)} science files and "
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
        f"Unreleased "
        f"{len(science_files)} science files and "
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

    released_flag = query_validation["data"]["released_flag"]
    release_type = query_validation["data"]["release_type"]
    query_params = query_validation["data"]["query_params"]

    if release_type == "release":
        release_type_handler(released_flag, query_params)
    elif release_type == "early-release":
        early_release_type_handler(query_params)
    elif release_type == "unrelease":
        unrelease_type_handler(query_params)

    return {
        "statusCode": 200,
        "body": json.dumps(
            f"Successful {release_type} action - "
            f"updated release status to '{released_flag}'"
        ),
    }
