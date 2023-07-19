import os
import time
import unittest

import boto3
import pytest
from botocore.exceptions import ClientError
from opensearchpy import RequestsHttpConnection

from sds_data_manager.lambda_code.SDSCode import queries
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.action import Action
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.client import Client
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.document import Document
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.index import Index


@pytest.mark.network()
class TestQueries(unittest.TestCase):
    def setUp(self):
        # Opensearch client Params
        os.environ[
            "OS_DOMAIN"
        ] = "search-sds-metadata-uum2vnbdbqbnh7qnbde6t74xim.us-west-2.es.amazonaws.com"
        os.environ["OS_PORT"] = "443"
        os.environ["OS_INDEX"] = "test_data"

        hosts = [{"host": os.environ["OS_DOMAIN"], "port": os.environ["OS_PORT"]}]

        secret_name = "OpenSearchPassword9643DC3D-uVH94BjrbF9u"
        region_name = "us-west-2"

        # Create a Secrets Manager client
        session = boto3.session.Session()
        client = session.client(service_name="secretsmanager", region_name=region_name)
        try:
            get_secret_value_response = client.get_secret_value(SecretId=secret_name)
        except ClientError as e:
            # For a list of exceptions thrown, see
            # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
            raise e

        # Decrypts secret using the associated KMS key.
        secret = get_secret_value_response["SecretString"]

        auth = ("master-user", secret)
        self.client = Client(
            hosts=hosts,
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connnection_class=RequestsHttpConnection,
        )

        os.environ["OS_ADMIN_USERNAME"] = "master-user"
        os.environ["OS_ADMIN_PASSWORD_LOCATION"] = secret
        body = {
            "mission": "imap",
            "level": "l0",
            "instrument": "mag",
            "date": "20230112",
            "version": "*",
            "extension": "pkts",
        }
        self.document = Document(Index(os.environ["OS_INDEX"]), 1, Action.INDEX, body)

    def test_queries(self):
        """tests that the queries lambda correctly returns the search results"""
        ## Arrange ##
        response_true = [
            {
                "_index": "test_data",
                "_type": "_doc",
                "_id": "1",
                "_score": 0.2876821,
                "_source": {
                    "mission": "imap",
                    "level": "l0",
                    "instrument": "mag",
                    "date": "20230112",
                    "version": "*",
                    "extension": "pkts",
                },
            }
        ]
        self.client.send_document(self.document)
        time.sleep(1)
        event = {"queryStringParameters": {"instrument": "mag"}}

        ## Act ##
        response_out = queries.lambda_handler(event, "")

        ## Assert ##
        assert response_out == response_true

    def tearDown(self):
        self.client.send_document(self.document, action_override=Action.DELETE)
