import json
import logging 
import os 
import sys
from .opensearch_utils.index import Index
from .opensearch_utils.client import Client
from .opensearch_utils.query import Query
from opensearchpy import OpenSearch, RequestsHttpConnection

logger=logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

def _create_open_search_client():
    logger.info("OS DOMAIN: " + os.environ["OS_DOMAIN"])
    hosts = [{"host":os.environ["OS_DOMAIN"], "port":int(os.environ["OS_PORT"])}]
    auth = (os.environ["OS_ADMIN_USERNAME"], os.environ["OS_ADMIN_PASSWORD_LOCATION"])
    return Client(hosts=hosts, http_auth=auth, use_ssl=True, verify_certs=True, connnection_class=RequestsHttpConnection)

def lambda_handler(event, context):
    logger.info("Received event: " + json.dumps(event, indent=2))
    # create the opensearch query from the API parameters
    query = Query(event["queryStringParameters"])
    client = _create_open_search_client()
    logger.info("Query: " + query.query_dsl())
    # search the opensearch instance
    search_result = client.search(query, Index(os.environ["OS_INDEX"]))
    logger.info("Query Search Results: " + json.dumps(search_result))
    return search_result