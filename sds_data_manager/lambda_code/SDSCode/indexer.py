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
        # TODO: change below logics to use new FilenameParser
        # when we create schema and write file metadata to DB
        filename_parsed = FilenameParser(filename)
        filename_parsed.upload_filepath()

        # setup a dictionary of metadata parmaeters to unpack in the
        # instrument table
        metadata_params = {
            "id": 1,
            "file_name": filename_parsed.upload_filepath()["body"],
            "instrument": filename_parsed.instrument,
            "data_level": filename_parsed.data_level,
            "descriptor": filename_parsed.descriptor,
            "start_date": filename_parsed.startdate,
            "end_date": filename_parsed.enddate,
            "ingestion_date": datetime.datetime.now(),
            "version": filename_parsed.version,
            "format": filename_parsed.extension,
        }

        # Add data to the corresponding instrument database
        with Session(engine) as session:
            if filename_parsed.instrument == "lo":
                data = models.LoMetadataTable(**metadata_params)
            elif filename_parsed.instrument == "hi":
                data = models.HiMetadataTable(**metadata_params)
            elif filename_parsed.instrument == "ultra":
                data = models.UltraMetadataTable(**metadata_params)
            elif filename_parsed.instrument == "hit":
                data = models.HITMetadataTable(**metadata_params)
            elif filename_parsed.instrument == "idex":
                data = models.IDEXMetadataTable(**metadata_params)
            elif filename_parsed.instrument == "swapi":
                data = models.SWAPIMetadataTable(**metadata_params)
            elif filename_parsed.instrument == "swe":
                data = models.SWEMetadataTable(**metadata_params)
            elif filename_parsed.instrument == "codice":
                data = models.CoDICEMetadataTable(**metadata_params)
            elif filename_parsed.instrument == "mag":
                data = models.MAGMetadataTable(**metadata_params)
            elif filename_parsed.instrument == "glows":
                data = models.GLOWSMetadataTable(**metadata_params)
            else:
                raise Exception(
                    f"Invalid instrument name in metadata. \
                                recieved: {filename_parsed.instrument}, but only \
                                    lo, hi, ultra, hit, idex, swapi, swe, codice, mag, \
                                    glows are valid"
                )

            session.add(data)
            session.commit()
