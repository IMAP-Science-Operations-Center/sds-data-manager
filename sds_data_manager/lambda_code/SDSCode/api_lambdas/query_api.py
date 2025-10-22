"""Contains the lambda handler for the 'query' data access API."""

import datetime
import json
import logging
import os
from collections import namedtuple

import boto3
import botocore
from sqlalchemy import func, select

from ..api_lambdas.utils import is_authenticated_user
from ..database import database as db
from ..database import models

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):  # noqa: PLR0912 PLR0915
    """Entry point to the query API lambda.

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
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    logger.info("Received event: " + json.dumps(event, indent=2))

    TableModels = namedtuple(
        "TableModels", ["science", "ancillary", "spice", "quicklook"]
    )

    table_models = TableModels(
        science=models.ScienceFiles,
        ancillary=models.AncillaryFiles,
        spice=models.SPICEFiles,
        quicklook=models.QuicklookFiles,
    )

    # add session, pick model like in indexer and add query to filter_as
    query_params = event["queryStringParameters"]
    # get desired table for query
    query_table = query_params.get("table", "science")

    logger.info(f"Querying table: {query_table}")
    model = getattr(table_models, query_table)

    # select the given table for the query
    query = select(model.__table__)
    if not is_authenticated_user(event):
        query = query.filter(model.released)

    # get a list of all valid search parameters
    valid_parameters = [
        column.key for column in model.__table__.columns if column.key not in ["id"]
    ]
    # Up until this point, valid_parameters are the same as the
    # columns in the selected table. And looks like we removed
    # the "id" column from the list. But we also need to add
    # 'end_date' to the list of valid_parameters but only for
    # the science table.
    if query_table != "ancillary":
        valid_parameters.append("end_date")
    valid_parameters.append("ingestion_start_date")
    valid_parameters.append("ingestion_end_date")

    # go through each query parameter to set up sqlalchemy query conditions
    for param, value in query_params.items():
        # skip the table parameter
        if param == "table":
            continue
        # confirm that the query parameter is valid
        if param not in valid_parameters:
            response = {
                "statusCode": 400,
                "body": json.dumps(
                    f"{param} is not a valid query parameter for {query_table} table. "
                    + f"Valid query parameters are: {valid_parameters}"
                ),
            }
            logger.debug(
                f"Received an invalid query parameter [{param}] for table "
                "{query_table}, valid options are: {valid_parameters}"
            )
            return response
        # check if we're search for start_date or end date or ingestion dates to
        # setup the correct "where" time condition
        if param == "start_date":
            query = query.where(
                model.start_date >= datetime.datetime.strptime(value, "%Y%m%d")
            )
        elif param == "end_date":
            # TODO: Need to discuss as a team how to handle date queries. For now,
            # the date queries will only look at the file start_date.
            query = query.where(
                model.start_date <= datetime.datetime.strptime(value, "%Y%m%d")
            )
        elif param == "ingestion_start_date":
            # filtering by ingestion date
            query = query.where(
                func.date(model.ingestion_date)
                >= datetime.datetime.strptime(value, "%Y%m%d").date()
            )
        elif param == "ingestion_end_date":
            query = query.where(
                func.date(model.ingestion_date)
                <= datetime.datetime.strptime(value, "%Y%m%d").date()
            )
        # all non-time string matching parameters
        else:
            query = query.where(getattr(model, param) == value)

    # We want to order the query returns by the filename
    # This will implicitly sort by: instrument, data level, descriptor, start_date, ...
    # Default for the table is by the ascending id so by insertion order
    # This fails for the SPICE table because it uses 'file_name'
    query = query.order_by(model.file_path)

    with db.Session() as session:
        search_results = session.execute(query).all()

    # Convert the search results (list of tuples) to a list of dicts
    search_results = [result._asdict() for result in search_results]

    # Check if those files exists in S3 before returning them
    bucket = os.environ["S3_BUCKET"]
    region = os.environ["REGION"]
    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
        endpoint_url=f"https://s3.{region}.amazonaws.com",
    )
    existing_files = []
    for result in search_results:
        s3_key = result["file_path"]
        # check if object exists
        try:
            s3_client.head_object(Bucket=bucket, Key=s3_key)
            existing_files.append(result)
        except Exception as e:
            logger.error(
                f"File not found in S3: {s3_key} but exists in DB - error: {e}"
            )

    search_results = existing_files
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

    # Format the response
    response = {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(search_results),  # returns a list of tuples
    }

    return response
