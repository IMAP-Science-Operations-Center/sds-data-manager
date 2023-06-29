import json
import logging
import os
import sys

import boto3
from opensearchpy import RequestsHttpConnection

from .opensearch_utils.client import Client
from .opensearch_utils.index import Index
from .opensearch_utils.query import Query

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def _create_open_search_client():
    logger.info("OS DOMAIN: " + os.environ["OS_DOMAIN"])
    hosts = [{"host": os.environ["OS_DOMAIN"], "port": int(os.environ["OS_PORT"])}]
    # TODO: remove hard-coded parameters
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name="us-west-2")
    response = client.get_secret_value(
        SecretId=os.environ["SECRET_ID"]
    )

    auth = (os.environ["OS_ADMIN_USERNAME"], response['SecretString'])

    return Client(
        hosts=hosts,
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connnection_class=RequestsHttpConnection,
    )


def lambda_handler(event, context):
    logger.info("Received event: " + json.dumps(event, indent=2))
    # create the opensearch query from the API parameters
    query = Query(event["queryStringParameters"])
    client = _create_open_search_client()
    logger.info("Query: " + str(query.query_dsl()))
    # search the opensearch instance
    search_result = client.search(query, Index(os.environ["OS_INDEX"]))
    logger.info("Query Search Results: " + json.dumps(search_result))

    # Format the response
    response = {
        "statusCode": 200,
        "body": json.dumps(search_result),  # Convert JSON data to a string
        "headers": {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'  # Allow CORS
        },
    }
    return response
