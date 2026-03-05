"""Define lambda to support the upload API.

To avoid any 307 redirects we use s3v4 signing method.
This method includes the region in the URL, so when the user uploads a file,
the URL will point directly to the correct regional S3 endpoint.
"""

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


def lambda_handler(event, context):
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
    # Check API key scope for upload restrictions
    request_ctx = event.get("requestContext", {})
    auth = request_ctx.get("authorizer", {})
    auth_ctx = auth.get("lambda", {})
    scope = auth_ctx.get("scope", "")

    # Deny upload access for read-only scope
    if scope == "read":
        return {
            "statusCode": 403,
            "body": json.dumps(
                "Upload access denied. Your API key has read-only permissions."
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

    s3_key_path = file_obj.construct_path()
    # Strip off the data directory to get the upload path + name
    # Must be posix style for the URL
    s3_key_path_str = str(
        s3_key_path.relative_to(imap_data_access.config["DATA_DIR"]).as_posix()
    )

    return _generate_signed_upload_response(s3_key_path_str)
