# Standard
import json
import logging
import os
import sys

# Installed
import boto3
from opensearchpy import RequestsHttpConnection

# Local
from .opensearch_utils.action import Action
from .opensearch_utils.client import Client
from .opensearch_utils.document import Document
from .opensearch_utils.index import Index
from .opensearch_utils.payload import Payload

# Logger setup
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

s3 = boto3.client("s3")


def _load_allowed_filenames():
    """Load the allowed filenames configuration from an S3 bucket.

    Returns
    -------
    dict
        The content of 'config.json', parsed into a Python dictionary.
    """

    # get the config file from the S3 bucket
    config_object = s3.get_object(
        Bucket=os.environ["S3_CONFIG_BUCKET_NAME"], Key="config.json"
    )
    file_content = config_object["Body"].read()
    return json.loads(file_content)


def _check_for_matching_filetype(pattern: dict, filename: str):
    """
    Checks whether a given filename matches a specific pattern.

    Parameters
    ----------
    pattern : dict
        The pattern to match the filename against.
    filename : str
        The filename to check.

    Returns
    -------
    dict or None
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


def _create_open_search_client():
    """Retrieve secrets from Secrets Manager and creates an Open Search client.

    This function retrieves the secret from the Secrets Manager and uses
    the secrets, along with other environment variables, to establish a
    secure connection to the OpenSearch cluster.

    Returns
    -------
    elasticsearch.Elasticsearch
        An instance of the OpenSearch client connected to the specified cluster.
    """
    hosts = [{"host": os.environ["OS_DOMAIN"], "port": int(os.environ["OS_PORT"])}]

    session = boto3.session.Session()
    client = session.client(
        service_name="secretsmanager", region_name=os.environ["REGION"]
    )
    response = client.get_secret_value(SecretId=os.environ["SECRET_ID"])

    auth = (os.environ["OS_ADMIN_USERNAME"], response["SecretString"])

    return Client(
        hosts=hosts,
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connnection_class=RequestsHttpConnection,
    )


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

    # Retrieve a list of allowed file types
    logger.info("Loading allowed filenames from configuration file in S3.")
    filetypes = _load_allowed_filenames()
    logger.info("Allowed file types: " + str(filetypes))

    # create opensearch client
    client = _create_open_search_client()
    # create an index
    index = Index(os.environ["OS_INDEX"])
    # create a payload
    document_payload = Payload()

    # We're only expecting one record, but for some reason the Records are a list object
    for record in event["Records"]:
        # Retrieve the Object name
        logger.info(f"Record Received: {record}")
        filename = record["s3"]["object"]["key"]

        logger.info(f"Attempting to insert {os.path.basename(filename)} into database")

        # Look for matching file types in the configuration
        for filetype in filetypes:
            metadata = _check_for_matching_filetype(
                filetype["pattern"], os.path.basename(filename)
            )
            if metadata is not None:
                break

        # Found nothing. This should probably send out an error notification
        # to the team, because how did it make its way onto the SDS?
        if metadata is None:
            logger.info("Found no matching file types to index this file against.")
            return None

        logger.info("Found the following metadata to index: " + str(metadata))

        # use the s3 path to file as the ID in opensearch
        s3_path = os.path.join(os.environ["S3_DATA_BUCKET"], filename)
        # create a document for the metadata and add it to the payload
        opensearch_doc = Document(index, s3_path, Action.CREATE, metadata)
        document_payload.add_documents(opensearch_doc)

    # send the paylaod to the opensearch instance
    client.send_payload(document_payload)
    client.close()
