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
    """Entry point to the release query API lambda.

    imap-data-access release --instrument --start-date --end-date --release-type --release-number (optional)

    Filename convention for release table records:
        imap_<instrument>_<descriptor>_<start_date>_<end_date>_<version>.<extension>

        The <descriptor> field options:

        withhold-data-release-<###> - it will support integer value associated to a release number.
        early-release  - it will support making an early release of selected files
        unrelease - it will support un-releasing selected files after they've been released.

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
        "start_time",
        "end_time",
        "release-type",
        "release-number",
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
                f"Received an invalid query parameter [{param}],"
                " valid options are: {valid_parameters}"
            )
            return response
        try:
            if param == "start_time":
                query = query.where(model.max_date_j2000 >= int(value))
            elif param == "end_time":
                query = query.where(model.min_date_j2000 <= int(value))
            elif param == "release-type":
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
        # TODO: should this only return no more than one record?
        search_results = session.execute(query).all()

    # TODO:
    # - download and read file for listing of products
    # - query science and ancillary tables for products in time range and update release to True
    #   except those in the release file.

    return {"statusCode": 200, "body": "Release API is working!"}
