"""Lambda function for release API endpoint."""

import datetime
import json
import logging
from collections import namedtuple

from sqlalchemy import func, select

from ..api_lambdas.utils import is_authenticated_user
from ..database import database as db
from ..database import models

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# TODO:
#   - For release-type, consider making it optional and default to withhold files if type isn't given.
#     This would make routine releases a simple api call to release files for a date range given, and
#     by default, checks for any related withhold files to process. "early-release" and "unrelease" would be
#     special cases that require the release-type param to be used.
#   - The release column may change from a boolean value to an integer representing
#     the release number the file pertains to. Needs further discussion
#   - Some of this code is borrowed from query_api.py. Refactor to reduce duplication

def lambda_handler(event, context):
    """Entry point for the release API lambda.

    This API applies release policy to science and ancillary products by updating
    their public visibility (`released` flag).

    Release workflows are driven by records in the `ReleaseFiles` table, where each
    record defines a time range and a release operation.

    Release file naming convention:
        imap_<instrument>_<descriptor>_<start_date>_<end_date>_<version>.<extension>

    Descriptor semantics:
        - withhold-data-release-<###>:
          Release all matching files for the period except those listed in the file.
        - early-release:
          Release only the files listed in the file before the scheduled release cadence.
        - unrelease:
          Mark previously released listed files as not released.

    Expected API query parameters:
        instrument, start_date, end_date, release_type, release_number (optional)

    Parameters
    ----------
    event : dict
        Input event containing `queryStringParameters`.
    context : LambdaContext
        Lambda runtime context object.
    """
    # Check authentication is valid
    if not is_authenticated_user(event):
        response = {
            "statusCode": 400,
            "body": json.dumps(
                f"API authentication failed: {event['body']}."),
        }
        logger.debug(
            f"API authentication failed: {event['body']}."
        )
        return response

    logger.info("Release Query Event: " + json.dumps(event, indent=2))

    # tables to query for release operations
    table_models = {
        "release": models.ReleaseFiles,
        "science": models.ScienceFiles,
        "ancillary": models.AncillaryFiles,
    }

    # add session, pick model
    query_params = event["queryStringParameters"]

    # get desired table for release query
    logger.info(f"Querying table: Release")
    model = table_models.get("release")

    # select the given table for the query
    query = select(model)

    # get a list of all valid search parameters
    valid_parameters = [
        "instrument",
        "start_date",
        "end_date",
        "release_type",
        "release_number",
    ]

    # go through each query parameter to set up sqlalchemy query conditions
    for param, value in query_params.items():
        # confirm that the query parameter is valid
        if param not in valid_parameters:
            response = {
                "statusCode": 400,
                "body": json.dumps(
                    f"{param} is not a valid query parameter. "
                    + f"Valid query parameters are: {valid_parameters}"
                ),
            }
            logger.debug(
                f"Received an invalid query parameter [{param}], valid options are: {valid_parameters}"
            )
            return response
        try:
            if param == "start_date":
                query = query.where(
                    model.start_date >= datetime.datetime.strptime(value, "%Y%m%d")
                )
            elif param == "end_date":
                # the date queries will only look at the file start_date.
                query = query.where(
                    model.end_date <= datetime.datetime.strptime(value, "%Y%m%d")
                )
            elif param == "release_type":
                valid_release_types = [
                    "early-release",
                    "unrelease",
                    "withhold-data",  # TODO: make this one default if release-type isn't given?
                ]
                if value not in valid_release_types:
                    response = {
                        "statusCode": 400,
                        "body": json.dumps(
                            f"{param} is not a valid release_type parameter. "
                            + f"Valid release_type parameters are: {valid_release_types}"
                        ),
                    }
                    logger.debug(
                        f"Received an invalid release_type parameter [{param}], valid options are: {valid_release_types}"
                    )
                    return response
                # filter release-type in filename using a "contains" query on the file path
                query = query.where(model.file_path.contains(value, autoescape=True))
        except ValueError:
            response = {
                "statusCode": 400,
                "body": json.dumps(f"Invalid value for {param}: {value}"),
            }
            logger.debug(f"Invalid value for {param}: {value}")
            return response

    # Keep only rows at the highest version from the filtered result set.
    filtered_subq = query.subquery()
    max_version_subq = select(func.max(filtered_subq.c.version)).scalar_subquery()
    query = select(filtered_subq).where(filtered_subq.c.version == max_version_subq)

    with db.Session() as session:
        # TODO: should this only return 1 or 0 results?
        search_results = session.execute(query).all()

    # Convert the search results (list of tuples) to a list of dicts
    search_results = [result._asdict() for result in search_results]

    # Convert datetimes to string values of format 'YYYYMMDD'
    # Also remove values that are not needed by users
    for result in search_results:
        result["start_date"] = result["start_date"].strftime("%Y%m%d")
        if result.get("end_date"):
            result["end_date"] = result["end_date"].strftime("%Y%m%d")
        d = result["ingestion_date"]
        if d.tzinfo is not None:
            # If the datetime has a timezone, convert it to UTC and remove the timezone
            d = d.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        result["ingestion_date"] = d.strftime("%Y%m%d %H:%M:%S")

    logger.info(
        "Found [%s] Query Search Results: %s", len(search_results), str(search_results)
    )

    # TODO: This function currently just queries the release table.
    #       It also needs to update release status of products
    #  - download and read release file found to get list of products
    #  - query science and ancillary tables for products in specified time range
    #  - write logic for handling withhold, unrelease, and early release files
    #       - withhold - update release to False for listed products. update all other files in release to True.
    #       - unrelease - update release to False for listed products.
    #       - early release - update release to True for listed products.
    return {"statusCode": 200, "body": json.dumps(search_results)}
