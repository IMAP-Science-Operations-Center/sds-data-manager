import json
import logging
import os

import boto3
import botocore

logger = logging.getLogger()
logging.basicConfig()
logger.setLevel(logging.INFO)


def http_response(header_type="text/html", status_code=200, body="Success"):
    """customize http response.

    Args:
        header_type (str, optional): [description]. Defaults to 'text/html'.
        status_code (int, optional): [description]. Defaults to 200.
        body (str, optional): [description]. Defaults to 'Success'.

    Returns:
        [json]: API response
    """
    return {
        "headers": {
            "Content-Type": header_type,
        },
        "statusCode": status_code,
        "body": body,
    }


def lambda_handler(event, context):
    """This lambda handler checks if this file exists or not. If file doesn't exist, it
    gives back an error. Otherwise, it returns pre-signed s3 url that user can use to
    donwload data from s3.

    Args:
        event (dict): input to lambda
        context : This is not used.
    """

    logger.info(event)

    one_day = 86400
    url_life = os.environ.get("URL_EXPIRE", one_day)

    if not event["rawQueryString"]:
        response_body = """No input given. It requires s3_uri.\n
                        s3_uri: full s3 URI. Eg. s3://bucket-name/filepath/filename.pkts
                        """
        return http_response(status_code=400, body=response_body)

    elif "s3_uri" in event["queryStringParameters"]:
        # parse s3 uri to get bucket and filepath
        # Eg. s3://bucket-name/filepath/filename.pkts
        s3_uri = event["queryStringParameters"]["s3_uri"]

        if "s3://" not in s3_uri:
            response_body = "Not valid S3 URI. Example input: s3://bucket/path/file.ext"
            return http_response(status_code=400, body=response_body)
        # Parse by '//', then parse by first occurence of '/'. Result would look like:
        # ['bucket-name', 'filepath/filename.pkts']
        parsed_list = s3_uri.split("//")[1].split("/", 1)
        bucket = parsed_list[0]
        filepath = parsed_list[1]

    else:
        response_body = """Did not find s3_uri input parameter.\n
                        s3_uri: full s3 URI. Eg. s3://bucket-name/filepath/filename.pkts
                        """
        return http_response(status_code=400, body=response_body)

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

    return http_response(header_type="application/json", body=json.dumps(response_body))
