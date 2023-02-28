import json
import logging
import os

import boto3

logger = logging.getLogger()
logging.basicConfig()
logger.setLevel(logging.INFO)


def _load_allowed_filenames():
    """
    Load the config.json file as a python dictionary.

    :return: dictionary object of file types and their attributes.
    """

    current_dir = os.path.dirname(__file__)
    config_file = os.path.join(current_dir, "config.json")

    with open(config_file) as f:
        data = json.load(f)
    return data


def _check_for_matching_filetype(pattern, filename):
    """
    Read a pattern from config.json and compare it to the desired filename.

    :param pattern: A file naming pattern from the config.json
    :param filename: String name of the desired file name.

    :return: The file_dictionary, or None if there is no match.
    """
    split_filename = filename.replace("_", ".").split(".")

    if len(split_filename) != len(pattern):
        return None

    i = 0
    file_dictionary = {}
    for field in pattern:
        if pattern[field] == "*":
            file_dictionary[field] = split_filename[i]
        elif pattern[field] == split_filename[i]:
            file_dictionary[field] = split_filename[i]
        else:
            return None
        i += 1

    return file_dictionary


def _generate_signed_upload_url(filename, tags=None):
    """
    Create a presigned url for a file in the SDS storage bucket.

    :param filename: Required.  A string representing the name of the object to upload.
    :param tags: Optional.  A dictionary that will be stored in the S3 object metadata.

    :return: A URL string if the file was found, otherwise None.
    """
    filetypes = _load_allowed_filenames()
    for filetype in filetypes:
        path_to_upload_file = filetype["path"]
        metadata = _check_for_matching_filetype(filetype["pattern"], filename)
        if metadata is not None:
            break

    if metadata is None:
        logger.info("Found no matching file types to index this file against.")
        return None

    bucket_name = os.environ["S3_BUCKET"]
    url = boto3.client("s3").generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": bucket_name[5:],
            "Key": path_to_upload_file + filename,
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

    if "filename" not in event["queryStringParameters"]:
        return {
            "statusCode": 400,
            "body": json.dumps("Please specify a filename to upload"),
        }

    filename = event["queryStringParameters"]["filename"]
    url = _generate_signed_upload_url(filename, tags=event["queryStringParameters"])

    if url is None:
        return {
            "statusCode": 400,
            "body": json.dumps(
                "A pre-signed URL could not be generated. Please ensure that the "
                "file name matches mission file naming conventions."
            ),
        }

    return {"statusCode": 200, "body": json.dumps(url)}
