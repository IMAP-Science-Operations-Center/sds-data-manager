import json
import logging
import os
import sys
import cognito_utils
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
    auth = (os.environ["OS_ADMIN_USERNAME"], os.environ["OS_ADMIN_PASSWORD_LOCATION"])
    return Client(
        hosts=hosts,
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connnection_class=RequestsHttpConnection,
    )


def lambda_handler(event, context):
    logger.info("Received event: " + json.dumps(event, indent=2))
    
    # Verify the user's cognito token
    verified_token = False
    try:
        token=event["headers"]["authorization"]
        verified_token = cognito_utils.verify_cognito_token(token)
    except Exception as e:
        logger.info(f"Authentication error: {e}")


    if not verified_token:
        logger.info("Supplied token could not be verified")
        return {
                'statusCode': 400,
                'body': json.dumps("Supplied token could not be verified")
            }
    
    # create the opensearch query from the API parameters
    query = Query(event["queryStringParameters"])
    client = _create_open_search_client()
    logger.info("Query: " + query.query_dsl())
    # search the opensearch instance
    search_result = client.search(query, Index(os.environ["OS_INDEX"]))
    logger.info("Query Search Results: " + json.dumps(search_result))
    return search_result
