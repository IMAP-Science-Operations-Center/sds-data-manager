"""Define lambda to support the upload API.

To avoid any 307 redirects we use s3v4 signing method.
This method includes the region in the URL, so when the user uploads a file,
the URL will point directly to the correct regional S3 endpoint.
"""

import datetime
import json
import logging
import os

import boto3
import botocore
import imap_data_access

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BUCKET_NAME = os.getenv("S3_BUCKET")
REGION = os.getenv("REGION")
# The default presigned url signature does not include the region information
# within the signature and we should be hitting the actual s3 region endpoint
# to avoid any 307 redirects. (Generally only an issue on newly created buckets
# where the DNS records haven't propagated yet)
S3_CLIENT = boto3.client(
    "s3",
    region_name=REGION,
    config=botocore.client.Config(signature_version="s3v4"),
    endpoint_url=f"https://s3.{REGION}.amazonaws.com",
)

# Science products that may not be uploaded. A file is rejected if it matches
# instrument/data_level/descriptor and its start date falls in
# [start_date, end_date). A None bound means unbounded on that side.
FORBIDDEN_SCIENCE_UPLOADS = [
    # HIT l0 files in this range were modified due to a FSW issue.
    # We should not override them with new files.
    {
        "instrument": "hit",
        "data_level": "l0",
        "descriptor": "raw",
        "start_date": None,
        "end_date": datetime.datetime(2026, 5, 19),
    },
]


def _upload_science_forbidden(
    file_obj: imap_data_access.file_validation.ImapFilePath,
) -> None | dict:
    """Return the forbidden-upload rule matching this file, or None if allowed."""
    if type(file_obj) is not imap_data_access.file_validation.ScienceFilePath:
        return
    file_start_date = datetime.datetime.strptime(file_obj.start_date, "%Y%m%d")
    for forbidden in FORBIDDEN_SCIENCE_UPLOADS:
        if (
            file_obj.instrument == forbidden["instrument"]
            and file_obj.data_level == forbidden["data_level"]
            and file_obj.descriptor == forbidden["descriptor"]
            and (
                forbidden["start_date"] is None
                or file_start_date >= forbidden["start_date"]
            )
            and (
                forbidden["end_date"] is None or file_start_date < forbidden["end_date"]
            )
        ):
            return forbidden
    return None


def _file_exists(s3_key_path):
    """Check if a file exists in the SDS storage bucket at this key."""
    try:
        S3_CLIENT.head_object(Bucket=BUCKET_NAME, Key=s3_key_path)
        # If the head_object operation succeeds, that means there
        # is a file already at the specified path, so return a 409
        return True
    except botocore.exceptions.ClientError:
        # No file exists
        return False


def _generate_signed_upload_response(s3_key_path, tags=None):
    """Create a presigned url for a file in the SDS storage bucket.

    Parameters
    ----------
    s3_key_path : str
        The fully qualified path of the object to upload.
    tags : dict, optional
         Additional S3 object metadata to add to the object.

    Returns
    -------
    Response with status code and a pre-signed URL for the object.
    """
    if _file_exists(s3_key_path):
        # We already have a file at this location, return a 409
        return {
            "statusCode": 409,
            "body": json.dumps(
                {
                    "error": "FileAlreadyExists",
                    "message": (
                        f"The file '{s3_key_path}' already exists. "
                        "Rename it or remove the existing file before uploading again."
                    ),
                }
            ),
        }
    # We know there isn't an object at this location, so
    # generate a pre-signed URL for the client to upload to
    url = S3_CLIENT.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": BUCKET_NAME,
            "Key": s3_key_path,
            "Metadata": tags or dict(),
        },
        ExpiresIn=3600,
    )

    return {"statusCode": 200, "body": json.dumps(url)}


def lambda_handler(event, context):  # noqa: PLR0911
    """Entry point to the upload API lambda.

    This function returns an S3 signed-URL based on the input filename,
    which the user can then use to upload a file into the SDS.

    Parameters
    ----------
    event : dict
        Specifically looking at the event['pathParameters']['proxy'], which
        specifies the filename to upload.
    context : None
        Currently not used

    Returns
    -------
    dict
        A pre-signed url where users can upload a data file to the SDS.

    """
    logger.info(f"Received upload request with event: {event}")
    # Check API key scope for upload restrictions
    request_ctx = event.get("requestContext", {})
    auth = request_ctx.get("authorizer", {})
    auth_ctx = auth.get("lambda", {})
    scope = auth_ctx.get("scope", "")
    api_key = auth_ctx.get("apiKey", "unknown")

    logger.info(f"Upload request received with scope: {scope}, api_key: {api_key}")

    # Deny upload access for read scope
    if scope == "read":
        logger.warning("Upload denied: read scope user attempted upload")
        return {
            "statusCode": 403,
            "body": json.dumps(
                "Upload access denied. Your API key has read permissions."
            ),
        }

    # Check if scope is missing (might be caught by authorizer first)
    if not scope:
        logger.warning("Upload denied: no scope found in authorizer context")
        return {
            "statusCode": 403,
            "body": json.dumps(
                "Upload access denied. Please provide a valid API key with "
                "upload permissions."
            ),
        }

    path_params = event.get("pathParameters", {}).get("proxy", None)
    logger.info("Parsing path parameters=[%s] from event=[%s]", path_params, event)

    if not path_params:
        return {
            "statusCode": 400,
            "body": json.dumps(
                "No filename given for the upload. Please provide a filename "
                "in the path. Eg. /upload/path/to/file/filename.pkts"
            ),
        }

    filename = os.path.basename(path_params)
    # Try to create a SPICE file first, if it fails, then science, then ancillary
    file_obj = None
    try:
        file_obj = imap_data_access.file_validation.generate_imap_file_path(filename)
    except ValueError:
        # Not a SPICE, ANCILLARY, or SCIENCE file, continue on to cadence files
        logger.info(
            f"Filename {filename} is not a valid SPICE, ANCILLARY, or SCIENCE file."
        )
        try:
            file_obj = imap_data_access.file_validation.CadenceFilePath(filename)
        except imap_data_access.ImapFilePath.InvalidImapFileError as e:
            # Did not match any file types
            logger.info(str(e))
            logger.error(
                f"Filename {filename} does not match ancillary, science, "
                f"cadence, or SPICE."
            )
            return {
                "statusCode": 400,
                "body": json.dumps(
                    f"error: {filename} does "
                    "not match ancillary, "
                    "science, cadence, or SPICE file "
                    "naming convention."
                ),
            }

    # Reject science files that don't match the new file format. Science files
    # must use the new version format (vMMM.mmmm); the legacy vXXX format is no
    # longer accepted for upload. We check the exact type so quicklook and
    # dependency files (subclasses of ScienceFilePath) are not affected.
    if (
        type(file_obj) is imap_data_access.file_validation.ScienceFilePath
        and file_obj.major_version is None
    ):
        logger.error(
            f"Upload denied: science file {filename} uses the legacy vXXX "
            f"version format, which is no longer accepted."
        )
        return {
            "statusCode": 400,
            "body": json.dumps(
                f"error: {filename} uses the legacy vXXX version format. "
                "Science files must use the new file format with a version "
                "of the form vMMM.mmmm."
            ),
        }

    # Reject any files that are forbidden from being uploaded
    if forbidden := _upload_science_forbidden(file_obj):
        logger.warning(f"Upload denied: {filename} matches forbidden rule {forbidden}")
        return {
            "statusCode": 403,
            "body": json.dumps(
                f"error: ({forbidden['instrument']}, {forbidden['data_level']}, "
                f"{forbidden['descriptor']}) files are forbidden from being "
                f"uploaded in range ({forbidden['start_date']}, "
                f"{forbidden['end_date']})."
            ),
        }

    s3_key_path = file_obj.construct_path()
    # Strip off the data directory to get the upload path + name
    # Must be posix style for the URL
    s3_key_path_str = str(
        s3_key_path.relative_to(imap_data_access.config["DATA_DIR"]).as_posix()
    )

    return _generate_signed_upload_response(s3_key_path_str)
