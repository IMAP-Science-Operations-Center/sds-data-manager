import json
import logging
import os

import boto3
from SDSCode.path_helper import InvalidScienceFileError, ScienceFilepathManager
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import database as db
from .database import models

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def _generate_signed_upload_url(key_path, tags=None):
    """
    Create a presigned url for a file in the SDS storage bucket.

    :param key_path: Required.  A string representing the name of the object to upload.
    :param tags: Optional.  A dictionary that will be stored in the S3 object metadata.

    :return: A URL string if the file was found, otherwise None.
    """
    bucket_name = os.environ["S3_BUCKET"]
    url = boto3.client("s3").generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": bucket_name,
            "Key": key_path,
            "Metadata": tags or dict(),
        },
        ExpiresIn=3600,
    )

    return url


def lambda_handler(event, context):
    """
    The entry point to the upload API lambda.

    This function returns an S3 signed-URL based on the input filename,
    which the user can then use to upload a file into the SDS.

    :param event: Dictionary
        Specifically only requires event['queryStringParameters']['filename']
        User-specified key:value pairs can also exist in the 'queryStringParameters',
        storing these pairs as object metadata.
    :param context: Unused

    :return: A pre-signed url where users can upload a data file to the SDS.
    """
    path_params = event.get("pathParameters", {}).get("proxy", None)
    logger.debug("Parsing path parameters=[%s] from event=" "[%s]", path_params, event)

    if not path_params:
        return {
            "statusCode": 400,
            "body": json.dumps(
                "No filename given for upload. "
                "Please provide a filename "
                "in the path. Eg. /upload/path/to/file/filename.pkts"
            ),
        }
    # TODO: Handle other filetypes other than science files
    #      The current ScienceFilepathManager only accepts filenames, not the full path
    filename = os.path.basename(path_params)
    try:
        science_file = ScienceFilepathManager(filename)
    except InvalidScienceFileError as e:
        logger.error(str(e))
        return {"statusCode": 400, "body": str(e)}

    s3_key_path = science_file.construct_upload_path()

    # Check for already existing file in the database
    with Session(db.get_engine()) as session:
        # query and check for a matching file path
        query = select(models.FileCatalog.__table__).where(
            models.FileCatalog.file_path == s3_key_path
        )
        result = session.execute(query).first()
        # return a 409 response if an existing file is found
        if result:
            response = {
                "statusCode": 409,
                "body": json.dumps(f"{s3_key_path} already exists."),
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            }
            return response

    url = _generate_signed_upload_url(s3_key_path)

    if url is None:
        return {
            "statusCode": 400,
            "body": json.dumps(
                "A pre-signed URL could not be generated. Please ensure that the "
                "file name matches mission file naming conventions."
            ),
        }

    return {"statusCode": 200, "body": json.dumps(url)}
