"""Module containing Lambda runtime code"""
# Standard
import datetime
import os
import boto3
import json


def main(event: dict, context):
    """Handler function for validating and recording archive times to RDS in response to output manifests.

    This function is an event handler called by the AWS Lambda upon the creation of an
    output manifest file.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide information about the invocation, function,
        and runtime environment.

     Returns
     -------
    : dict
        A custom dictionary of information reporting the lambda event that was triggered
    """
    print("Archiver Lambda Triggered.")
    print(event)
    print(context)

    # Environment Variables
    processing_path = os.environ['PROCESSING_PATH']
    archive_path = os.environ['ARCHIVE_PATH']

    # Step Function Inputs
    output_manifest_filename = event['OUTPUT_MANIFEST_FILENAME']
    print(f"Working in dropbox bucket: {processing_path}")

    print('Printing file location')
    file_s3_path = f"{processing_path}/{output_manifest_filename}"
    print(file_s3_path)

    #TODO: add database access
    files = []
    for file in files:
        filename = AnyFilename(file['filename'])
        archive_path = filename.generate_prefixed_path(archive_path)
        smart_copy_file(filename.path,
                        archive_path,
                        delete=True)

    response = {
        "OUTPUT_MANIFEST_FILENAME": output_manifest_filename
    }
    return response
