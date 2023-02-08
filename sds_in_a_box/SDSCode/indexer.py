import json
import urllib.parse
import boto3
import logging 
import os 
import sys
from sds_in_a_box.SDSCode.opensearch_utils.document import Document
from sds_in_a_box.SDSCode.opensearch_utils.index import Index
from sds_in_a_box.SDSCode.opensearch_utils.payload import Payload
from sds_in_a_box.SDSCode.opensearch_utils.action import Action
from sds_in_a_box.SDSCode.opensearch_utils.client import Client
from opensearchpy import OpenSearch, RequestsHttpConnection

logger=logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

s3 = boto3.client('s3')

def _load_allowed_filenames():
    # Rather than storing the configuration locally, we should store the configuration somewhere where things can be changed on the fly.  
    # For example, a dynamodb table or a section in opensearch
    current_dir = os.path.dirname(__file__)
    config_file = os.path.join(current_dir, "config.json")
    
    with open(config_file) as f:
        data = json.load(f)
    return data

def _check_for_matching_filetype(pattern, filename):
    
    split_filename = filename.replace("_", ".").split(".")

    if len(split_filename) != len(pattern):
        return None
    
    i = 0
    file_dictionary = {}
    for field in pattern:
        if pattern[field] == '*':
            file_dictionary[field] = split_filename[i]
        elif pattern[field] == split_filename[i]:
            file_dictionary[field] = split_filename[i]
        else:
            return None
        i += 1
    
    return file_dictionary

def _create_open_search_client():
    hosts = [{"host":os.environ["OS_DOMAIN"], "port":int(os.environ["OS_PORT"])}]
    auth = (os.environ["OS_ADMIN_USERNAME"], os.environ["OS_ADMIN_PASSWORD_LOCATION"])
    return Client(hosts=hosts, http_auth=auth, use_ssl=True, verify_certs=True, connnection_class=RequestsHttpConnection)

def lambda_handler(event, context):
    logger.info("Received event: " + json.dumps(event, indent=2))

    # Retrieve a list of allowed file types
    filetypes = _load_allowed_filenames()
    logger.info("Allowed file types: " + str(filetypes))

    # create opensearch client
    client = _create_open_search_client()
    # create an index
    index = Index(os.environ["OS_INDEX"])
    # create a payload
    document_payload = Payload()

    # We're only expecting one record, but for some reason the Records are a list object
    for record in event['Records']:
        
        # Retrieve the Object name
        logger.info(f'Record Received: {record}')
        bucket = record['s3']['bucket']['name']
        filename = record['s3']['object']['key']

        logger.info(f"Attempting to insert {filename} into database")

        # Look for matching file types in the configuration
        for filetype in filetypes:
            metadata = _check_for_matching_filetype(filetype['pattern'], filename)
            if metadata is not None:
                break
        
        #Found nothing.  This should probably send out an error notification to the team, because how did it make its way onto the SDC?
        if metadata is None:
            logger.info(f"Found no matching file types to index this file against.")
            return None
        
        # Rather than returning the metadata, we should insert it into the DB
        logger.info("Found the following metadata to index: " + str(metadata))

        # create a document for the metadata and add it to the payload
        opensearch_doc = Document(index, filename, Action.CREATE, metadata)
        document_payload.add_documents(opensearch_doc)

    # send the paylaod to the opensearch instance
    client.send_payload(document_payload)
    client.close()