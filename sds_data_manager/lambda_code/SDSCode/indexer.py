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
        if filepath["statusCode"] == 400:
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
            "ingestion_date": datetime.datetime.now(),
            "version": filename_parsed.version,
            "extension": filename_parsed.extension,
        }

        # Add data to the corresponding instrument database
        with Session(engine) as session:
            if filename_parsed.instrument == "lo":
                data = models.LoTable(**metadata_params)
            elif filename_parsed.instrument == "hi":
                data = models.HiTable(**metadata_params)
            elif filename_parsed.instrument == "ultra":
                data = models.UltraTable(**metadata_params)
            elif filename_parsed.instrument == "hit":
                data = models.HITTable(**metadata_params)
            elif filename_parsed.instrument == "idex":
                data = models.IDEXTable(**metadata_params)
            elif filename_parsed.instrument == "swapi":
                data = models.SWAPITable(**metadata_params)
            elif filename_parsed.instrument == "swe":
                data = models.SWETable(**metadata_params)
            elif filename_parsed.instrument == "codice":
                data = models.CoDICETable(**metadata_params)
            elif filename_parsed.instrument == "mag":
                data = models.MAGTable(**metadata_params)
            elif filename_parsed.instrument == "glows":
                data = models.GLOWSTable(**metadata_params)
            # FileParser already confirmed that the file has a valid
            # instrument name.

            session.add(data)
            session.commit()
