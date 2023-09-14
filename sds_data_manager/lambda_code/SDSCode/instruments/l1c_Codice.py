"""Lambda runtime code that triggers off of arrival of data into S3 bucket.
"""
# Standard
import os
import json
from datetime import datetime
# Installed
import boto3

#TODO: ability to access database, EFS, calibration data, etc.

def lambda_handler(event: dict, context):
    """Handler function"""
    print(event)
    print(context)

    now = datetime.now()
    print("Now time is")
    print(now)

    # Get the environment variables
    bucket = os.environ['PROCESSING_PATH']
    prefix = os.environ['INSTRUMENT_SOURCES']
    target_bucket = os.environ["INSTRUMENT_TARGET"]

    # Retrieves objects in the S3 bucket under the given prefix
    try:
        s3 = boto3.client('s3')
        object_list = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)["Contents"]
        print(object_list)
    except KeyError as ke:
        print("No files present.")

    #TODO: this state will change based on availability of data
    #TODO: we need to think about what needs to be passed into the container
    return {
        "STATE": "SUCCESS",
        "JOB_NAME": os.environ['PROCESSING_NAME'],
        'COMMAND': ["packet-ingest", f"s3://{bucket}/{target_bucket}", "-v"],
    }