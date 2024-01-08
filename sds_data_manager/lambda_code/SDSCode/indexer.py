# Standard
import datetime
import json
import logging
import os
import sys

# Installed
import boto3
from SDSCode.database import models
from SDSCode.database.database import engine
from sqlalchemy.orm import Session

# Local
from .path_helper import FilenameParser

# Logger setup
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

s3 = boto3.client("s3")


def lambda_handler(event, context):
    """Handler function for creating metadata, adding it to the payload,
    and sending it to the opensearch instance.

    This function is an event handler called by the AWS Lambda upon the creation of an
    object in a s3 bucket.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.
    """
    logger.info("Received event: " + json.dumps(event, indent=2))

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    # We're only expecting one record, but for some reason the Records are a list object
    for record in event["Records"]:
        # Retrieve the Object name
        logger.info(f"Record Received: {record}")
        filename = record["s3"]["object"]["key"]

        logger.info(f"Attempting to insert {os.path.basename(filename)} into database")
        filename_parsed = FilenameParser(filename)
        filepath = filename_parsed.upload_filepath()

        # confirm that the file is valid
        if filepath["statusCode"] != 200:
            logger.error(filepath["body"])
            break

        # setup a dictionary of metadata parameters to unpack in the
        # instrument table
        metadata_params = {
            "file_path": filepath["body"],
            "instrument": filename_parsed.instrument,
            "data_level": filename_parsed.data_level,
            "descriptor": filename_parsed.descriptor,
            "start_date": filename_parsed.startdate,
            "end_date": filename_parsed.enddate,
            "ingestion_date": datetime.datetime.now(datetime.timezone.utc),
            "version": filename_parsed.version,
            "extension": filename_parsed.extension,
        }

        # The model lookup is used to match the instrument data
        # to the correct postgres table based on the instrument name.
        model_lookup = {
            "lo": models.LoTable,
            "hi": models.HiTable,
            "ultra": models.UltraTable,
            "hit": models.HITTable,
            "idex": models.IDEXTable,
            "swapi": models.SWAPITable,
            "swe": models.SWETable,
            "codice": models.CoDICETable,
            "mag": models.MAGTable,
            "glows": models.GLOWSTable,
        }

        # FileParser already confirmed that the file has a valid
        # instrument name.
        data = model_lookup[filename_parsed.instrument](**metadata_params)

        # Add data to the corresponding instrument database
        with Session(engine) as session:
            session.add(data)
            session.commit()
