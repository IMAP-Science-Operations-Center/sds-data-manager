# Standard
import json
import logging
import os
import sys

# Installed
import boto3
from opensearchpy import RequestsHttpConnection

# Local
from .opensearch_utils.client import Client
from .opensearch_utils.index import Index
from .opensearch_utils.query import Query

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def _create_open_search_client():
    """Creates and returns an OpenSearch client.

    This function fetches environment variables to set up an
    OpenSearch client. It uses the AWS Secrets Manager to
    retrieve secure information for authentication.

    Returns
    -------
    Client
        A Client object that's connected to the specified OpenSearch cluster.
        This client is set to use SSL for secure connections and verifies certificates.
        It also uses the RequestsHttpConnection class for handling HTTP requests.

    Note
    ----
    This function is currently using hard-coded parameters for the
    AWS Secrets Manager session.
    """
    logger.info("OS DOMAIN: " + os.environ["OS_DOMAIN"])
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
    """Handler function for making queries.

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
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

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
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",  # Allow CORS
        },
    }
    return response
