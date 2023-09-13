"""Lambda runtime code that triggers off of arrival of data into receiver S3 bucket, and
creates a manifest file that will trigger the L0 processing step.
"""
# Standard
import os
import json
from datetime import datetime
# Installed
import boto3

# Full Success
FULL_SUCCESS = "FULL_SUCCESS"
# Partial Success
PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
# Waiting
WAITING_FOR_FILES = "WAITING"
ERROR_STATE = -1


def main(event: dict, context):
    """Handler function that generates manifest file"""
    print(event)
    print(context)

    now = datetime.now()
    print("Now time is")
    print(now)

    data_receiver_bucket = json.loads(os.environ['INPUT_BUCKETS'])[0]

    # Retrieves objects in the S3 bucket
    try:
        s3 = boto3.client('s3')
        object_list = s3.list_objects(Bucket=data_receiver_bucket)["Contents"]
        print(object_list)
    except KeyError as ke:
        print("No files present.")
