import os
import unittest
from operator import contains

import boto3
from moto import mock_s3

from sds_data_manager.lambda_code.SDSCode.download_query_api import lambda_handler


class TestDownloadQueryAPI(unittest.TestCase):
    mock_s3 = mock_s3()

    # Mocked AWS Credentials for moto
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"

    def setUp(self):
        self.mock_s3.start()
        self.bucket_name = "download-query-api"
        self.s3_client = boto3.client("s3")

        # create mock s3 bucket
        self.s3_client.create_bucket(
            Bucket=self.bucket_name,
            CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
        )
        # upload a file
        self.s3_filepath = "test-data/science_block_20221116_163611Z_idle.bin"
        self.local_filepath = f"tests/unit/{self.s3_filepath}"
        self.s3_client.upload_file(
            self.local_filepath, self.bucket_name, self.s3_filepath
        )
        file_list = self.s3_client.list_objects(Bucket=self.bucket_name)["Contents"]

        assert len(file_list) == 1

    def test_object_exists_with_s3_uri(self):
        """Test that objects exist in s3"""
        self.event = {
            "version": "2.0",
            "routeKey": "$default",
            "rawPath": "/",
            "rawQueryString": f"s3_uri=s3://{self.bucket_name}/{self.s3_filepath}",
            "queryStringParameters": {
                "s3_uri": f"s3://{self.bucket_name}/{self.s3_filepath}"
            },
        }

        response = lambda_handler(event=self.event, context=None)
        assert response["statusCode"] == 200
        assert contains(response["body"], "download_url")

    def test_object_exists_with_s3_uri_fails(self):
        """Test that objects exist in s3 fails"""
        self.event = {
            "version": "2.0",
            "routeKey": "$default",
            "rawPath": "/",
            "rawQueryString": f"s3_uri=s3://{self.bucket_name}/bad_path/bad_file.txt",
            "queryStringParameters": {
                "s3_uri": f"s3://{self.bucket_name}/bad_path/bad_file.txt"
            },
        }

        response = lambda_handler(event=self.event, context=None)
        assert response["statusCode"] == 404

    def test_input_parameters_missing(self):
        """Test that required input parameters exist"""
        self.empty_para_event = {
            "version": "2.0",
            "routeKey": "$default",
            "rawPath": "/",
            "rawQueryString": "",
        }

        self.bad_para_event = {
            "version": "2.0",
            "routeKey": "$default",
            "rawPath": "/",
            "rawQueryString": f"bad_input={self.s3_filepath}",
            "queryStringParameters": {"bad_input": f"{self.s3_filepath}"},
        }

        response = lambda_handler(event=self.empty_para_event, context=None)
        assert response["statusCode"] == 400

        response = lambda_handler(event=self.bad_para_event, context=None)
        assert response["statusCode"] == 400

    def tearDown(self):
        self.mock_s3.stop()


if __name__ == "__main__":
    unittest.main()
