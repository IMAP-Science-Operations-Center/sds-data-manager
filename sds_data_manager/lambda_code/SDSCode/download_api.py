# Standard
import json
import logging
import os

# Installed
import boto3
import botocore

# Logger setup
logger = logging.getLogger()
logging.basicConfig()
logger.setLevel(logging.INFO)


def http_response(headers=None, status_code=200, body="Success"):
    """Customizes HTTP response for the lambda function.

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
    if headers is None:
        headers = (
            {
                "Content-Type": "text/html",
            },
        )
    return {
        "headers": headers,
        "statusCode": status_code,
        "body": body,
    }


def lambda_handler(event, context):
    """This lambda handler checks if this file exists or not. If file doesn't exist, it
    gives back an error. Otherwise, it returns pre-signed s3 url that user can use to
    download data from s3.

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
    one_day = 86400
    url_life = os.environ.get("URL_EXPIRE", one_day)
    path_params = event.get("pathParameters", {}).get("proxy", None)
    logger.debug("Parsing path parameters=[%s] from event=[%s]", path_params, event)
    if not path_params:
        response_body = (
            "No file requested for download. Please provide a filename "
            "in the path. Eg. /download/path/to/file/filename.pkts"
        )
        return http_response(status_code=400, body=response_body)

    bucket = os.environ.get("S3_BUCKET")
    filepath = path_params

    s3_client = boto3.client("s3")

    # check if object exists
    try:
        s3_client.head_object(Bucket=bucket, Key=filepath)
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "404":
            # object doesn't exist
            return http_response(status_code=404, body="File not found in S3.")
        else:
            # fails due to another error
            return http_response(status_code=e.response["Error"]["Code"], body=str(e))

    pre_signed_url = s3_client.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": filepath}, ExpiresIn=url_life
    )
    response_body = {"download_url": pre_signed_url}
    # The 302 response needs a "Location" header with the pre-signed URL
    # to indicate where the redirect needs to point on the client side.
    headers = {"Content-Type": "text/html", "Location": pre_signed_url}
    return http_response(
        headers=headers, status_code=302, body=json.dumps(response_body)
    )
