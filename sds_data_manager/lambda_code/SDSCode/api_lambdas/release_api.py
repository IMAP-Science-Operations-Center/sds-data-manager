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
    logger.debug("Release Query Event: " + json.dumps(event, indent=2))

    TableModels = namedtuple(
        "TableModels", ["release", "science", "ancillary"]
    )

    table_models = TableModels(
        science=models.ScienceFiles,
        ancillary=models.AncillaryFiles,
        release=models.ReleaseFiles
    )

    # add session, pick model like in indexer and add query to filter_as
    query_params = event["queryStringParameters"]
    # get desired table for query
    query_table = "release"

    logger.info(f"Querying table: {query_table}")
    model = getattr(table_models, query_table)

    # select the given table for the query
    query = select(model.__table__)
    if not is_authenticated_user(event):
        query = query.filter(model.released)

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
            if param == "start_time":
                query = query.where(model.max_date_j2000 >= int(value))
            elif param == "end_time":
                query = query.where(model.min_date_j2000 <= int(value))
            elif param == "release-type":
                valid_release_types = [
                    "early-release",
                    "unrelease",
                    "withhold-data",  # TODO: make this one default if release-type isn't given?
                ]
                if param not in valid_release_types:
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
    with db.Session() as session:
        # TODO: should this return no more than one record?
        search_results = session.execute(query).all()

    # TODO:
    #  - download and read file to get list of products
    #  - query science and ancillary tables for products in specified time range
    #  - write logic for handling withhold, unrelease, and early release files
    #       - withhold - update release to False for listed products. update all other files in release to True.
    #       - unrelease - update release to False for listed products.
    #       - early release - update release to True for listed products.

    # TODO: for release-type, consider making it optional and default to withhold files if type isn't given.
    #  This makes our regular releases a simple api call to release files for a date range given, and
    #  by default, checks for any related withhold files to process. "early-release" and "unrelease" would be
    #  special cases that require the release-type param to be used.

    return {"statusCode": 200, "body": "Release API is working!"}
