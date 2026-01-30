"""Define lambda to support the download API."""

import json
import logging
import os

import boto3
import botocore
import imap_data_access

from ..api_lambdas.utils import is_authenticated_user
from ..database import database as db
from ..database import models

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def is_released(s3_key):
    """Check if the file is marked as released in the database.

    Parameters
    ----------
    s3_key : str
        The S3 key path of the file to check.

    Returns
    -------
    bool
        True if the file is released, False otherwise.
    """
    if "test_data" in s3_key:
        # Default to released for any test data files for our CI testing
        return True
    try:
        filename = os.path.basename(s3_key)
        file_obj = imap_data_access.file_validation.generate_imap_file_path(filename)

        # Determine which table to use based on file type using isinstance
        if isinstance(file_obj, imap_data_access.file_validation.SPICEFilePath):
            # NOTE: the spin and repoint files are not in the SPICE table, they
            #       are in separate tables, so there would have to be other
            #       logic here to separate out those file types if we wanted that.
            return True  # SPICE files are always released
        elif isinstance(file_obj, imap_data_access.file_validation.AncillaryFilePath):
            table = models.AncillaryFiles
        elif isinstance(file_obj, imap_data_access.file_validation.ScienceFilePath):
            table = models.ScienceFiles
        else:
            return False

        with db.Session() as session:
            file_record = session.query(table).filter(table.file_path == s3_key).first()
            if file_record and hasattr(file_record, "released"):
                return file_record.released
    except Exception as e:
        # If any error occurs, log it and default to not released for security
        logger.error(f"Error checking released status: {e!s}")

    return False


def http_response(headers=None, status_code=200, body="Success"):
    """Customize HTTP response for the lambda function.

    Parameters
    ----------
    headers : dict, optional
        Content headers for the response, defaults to Content-type: text/html.
    status_code : int, optional
        HTTP status code indicating the result of the operation, defaults to 200.
    body : str, optional
        The content of the response, defaults to 'Success'.

    Returns
    -------
    dict
        A dictionary containing headers, status code, and body, designed to be returned
        by a Lambda function as an API response.

    """
    headers = headers or {"Content-Type": "text/html"}
    return {
        "headers": headers,
        "statusCode": int(status_code),
        "body": body,
    }


def lambda_handler(event, context):
    """Entry point to the download API lambda.

    Check if this file exists or not. If file doesn't exist, it gives back an
    error. Otherwise, it returns pre-signed s3 url that user can use to download
    data from s3.

    To avoid any 307 redirects we use s3v4 signing method.
    This method includes the region in the URL, so when the user uploads a file,
    the URL will point directly to the correct regional S3 endpoint.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide information
        about the invocation, function, and runtime environment.

    Returns
    -------
    dict
        The response from the function which could either be a pre-signed
        S3 URL in case of successful operation or an error message with
        corresponding status code in case of failure.

    """
    logger.info("Received event: " + json.dumps(event, indent=2))

    one_day = 86400
    url_life = os.getenv("URL_EXPIRE", one_day)
    path_params = event.get("pathParameters", {}).get("proxy", None)
    logger.debug("Parsing path parameters=[%s] from event=[%s]", path_params, event)
    if not path_params:
        response_body = (
            "No file requested for download. Please provide a filename "
            "in the path. Eg. /download/path/to/file/filename.pkts"
        )
        return http_response(status_code=400, body=response_body)

    bucket = os.getenv("S3_BUCKET")
    region = os.getenv("REGION")
    filepath = path_params
    raw_path = event.get("rawPath")

    if (
        not is_authenticated_user(event)
        and raw_path.startswith("/ialirt-download/")
        and (filepath.startswith("logs/") or filepath.startswith("packets/"))
    ):
        return http_response(
            status_code=403,
            body="I-ALiRT logs and packet data are not publicly downloadable.",
        )

    # The default presigned url signature does not include the region information
    # within the signature and we should be hitting the actual s3 region endpoint
    # to avoid any 307 redirects. (Generally only an issue on newly created buckets
    # where the DNS records haven't propagated yet)
    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
        endpoint_url=f"https://s3.{region}.amazonaws.com",
    )

    # check if object exists
    try:
        s3_client.head_object(Bucket=bucket, Key=filepath)
    except botocore.exceptions.ClientError as e:
        # Log the error and return a 404 response even if it is something
        # different like a 403 from the backend, which just means the action
        # can't be performed like only providing a filename without a path.
        logger.error(
            "Error: %s\n%s", e.response["Error"]["Code"], e.response["Error"]["Message"]
        )
        return http_response(
            status_code=404,
            body=(
                "File not found, make sure you include the full path to the file in "
                "the request, e.g. /download/path/to/file/filename.pkts."
            ),
        )

    if (
        not is_authenticated_user(event)
        and not is_released(filepath)
        and not raw_path.startswith("/ialirt-download/")
    ):
        return http_response(
            status_code=403,
            body=f"The file {filepath} is not a part of a public release yet.",
        )

    pre_signed_url = s3_client.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": filepath}, ExpiresIn=url_life
    )
    logger.info(f"Downloading {filepath}")
    response_body = {"download_url": pre_signed_url}
    # The 302 response needs a "Location" header with the pre-signed URL
    # to indicate where the redirect needs to point on the client side.
    headers = {"Content-Type": "text/html", "Location": pre_signed_url}
    return http_response(
        headers=headers, status_code=302, body=json.dumps(response_body)
    )
