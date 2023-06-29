# Standard
import os
import time
import unittest
import json
# Installed
import boto3
import pytest
from botocore.exceptions import ClientError
from opensearchpy import RequestsHttpConnection
# Local
from sds_data_manager.lambda_code.SDSCode import queries
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.action import Action
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.client import Client
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.document import Document
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.index import Index


@pytest.mark.network()
class TestQueries(unittest.TestCase):
    def setUp(self):
        # Opensearch client Params
        # TODO: there has to be a better way
        os.environ[
            "OS_DOMAIN"
        ] = "search-sdsmetadatadomain-dev-i3bnjqingkrphg2crwdcwqabqe.us-west-2.es.amazonaws.com"

        os.environ["OS_PORT"] = "443"
        os.environ["OS_INDEX"] = "test_data"

        hosts = [{"host": os.environ["OS_DOMAIN"], "port": os.environ["OS_PORT"]}]

        secret_name = "sdp-database-creds"
        region_name = "us-west-2"

        # Create a Secrets Manager client
        session = boto3.session.Session()
        client = session.client(
            service_name='secretsmanager',
            region_name=region_name)

        try:
            get_secret_value_response = client.get_secret_value(
                SecretId=secret_name
            )
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
        os.environ["SECRET_ID"] = secret_name
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
                "_score": 0.18232156,
                "_source": {
                    "mission": "imap",
                    "level": "l0",
                    "instrument": "mag",
                    "date": "20230112",
                    "version": "*",
                    "extension": "pkts",
                }
            }
        ]
        self.client.send_document(self.document)
        time.sleep(1)
        event = {"queryStringParameters": {"instrument": "mag"}}

        ## Act ##
        response = queries.lambda_handler(event, "")
        response_out = json.loads(response['body'])

        for res in response_out:
            res['_score'] = None
        for res in response_true:
            res['_score'] = None

        assert response_out == response_true

    def tearDown(self):
        self.client.send_document(self.document, action_override=Action.DELETE)
