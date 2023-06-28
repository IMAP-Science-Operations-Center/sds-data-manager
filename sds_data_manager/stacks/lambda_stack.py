# Standard
import pathlib
# Installed
import aws_cdk as cdk
from constructs import Construct
from aws_cdk import (
    Stack,
    Environment,
    aws_lambda as lambda_,
    aws_lambda_event_sources,
    aws_lambda_python_alpha as lambda_alpha_,
    aws_s3 as s3
)
# Local
from .opensearch_stack import OpenSearch
from .sds_data_manager_stack import SdsDataManager


class OpenSearchLambdas(Stack):
    """Stack for OpenSearch Lambdas."""
    def __init__(self, scope: Construct,
                 construct_id: str,
                 sds_id: str,
                 opensearch: OpenSearch,
                 data_manager: SdsDataManager,
                 env: Environment,
                 **kwargs) -> None:
        """LambdaStack

        Parameters
        ----------
        scope : App
        construct_id : str
        sds_id: str
        opensearch: object
            Instance of OpenSearch
        data_manager: object
            Instance of SdsDataManager
        env : Environment
            Account and region
        """
        super().__init__(scope, construct_id, env=env, **kwargs)

        indexer_lambda = lambda_alpha_.PythonFunction(
            self,
            id="IndexerLambda",
            function_name=f"file-indexer-{sds_id}",
            entry=str(pathlib.Path(__file__).parent.joinpath("..", "lambda_code").resolve()),
            index="SDSCode/indexer.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=cdk.Duration.minutes(15),
            memory_size=1000,
            environment={
                "OS_ADMIN_USERNAME": "master-user",
                "OS_ADMIN_PASSWORD_LOCATION": opensearch.os_secret.secret_value.unsafe_unwrap(),
                "OS_DOMAIN": opensearch.sds_metadata_domain.domain_endpoint,
                "OS_PORT": "443",
                "OS_INDEX": "metadata",
                "S3_DATA_BUCKET": data_manager.data_bucket.s3_url_for_object(),
                "S3_CONFIG_BUCKET_NAME": f"sds-config-{sds_id}"
            },
        )

        indexer_lambda.add_event_source(
            aws_lambda_event_sources.S3EventSource(data_manager.data_bucket, events=[s3.EventType.OBJECT_CREATED])
        )
        indexer_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        # Adding Opensearch permissions
        indexer_lambda.add_to_role_policy(opensearch.opensearch_all_http_permissions)
        # Adding s3 read permissions to get config.json
        indexer_lambda.add_to_role_policy(data_manager.s3_read_policy)

        # upload API lambda
        upload_api_lambda = lambda_alpha_.PythonFunction(
            self,
            id="UploadAPILambda",
            function_name=f"upload-api-handler-{sds_id}",
            entry=str(pathlib.Path(__file__).parent.joinpath("..", "lambda_code").resolve()),
            index="SDSCode/upload_api.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=cdk.Duration.minutes(15),
            memory_size=1000,
            environment={
                "S3_BUCKET": data_manager.data_bucket.s3_url_for_object(),
                "S3_CONFIG_BUCKET_NAME": f"sds-config-{sds_id}",
            },
        )
        upload_api_lambda.add_to_role_policy(data_manager.s3_write_policy)
        upload_api_lambda.add_to_role_policy(data_manager.s3_read_policy)
        upload_api_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        # query API lambda
        query_api_lambda = lambda_alpha_.PythonFunction(
            self,
            id="QueryAPILambda",
            function_name=f"query-api-handler-{sds_id}",
            entry=str(pathlib.Path(__file__).parent.joinpath("..", "lambda_code").resolve()),
            index="SDSCode/queries.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            environment={
                "OS_ADMIN_USERNAME": "master-user",
                "OS_ADMIN_PASSWORD_LOCATION": opensearch.os_secret.secret_value.unsafe_unwrap(),
                "OS_DOMAIN": opensearch.sds_metadata_domain.domain_endpoint,
                "OS_PORT": "443",
                "OS_INDEX": "metadata",
            },
        )
        query_api_lambda.add_to_role_policy(opensearch.opensearch_read_only_policy)

        # download query API lambda
        download_query_api = lambda_alpha_.PythonFunction(
            self,
            id="DownloadQueryAPILambda",
            function_name=f"download-query-api-{sds_id}",
            entry=str(pathlib.Path(__file__).parent.joinpath("..", "lambda_code").resolve()),
            index="SDSCode/download_query_api.py",
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=cdk.Duration.seconds(60),
        )
        download_query_api.add_to_role_policy(opensearch.opensearch_all_http_permissions)
        download_query_api.add_to_role_policy(data_manager.s3_read_policy)

        self.lambda_functions = {
            'upload': {
                'function': upload_api_lambda,
                'httpMethod': 'POST'
            },
            'query': {
                'function': query_api_lambda,
                'httpMethod': 'GET'
            },
            'download': {
                'function': download_query_api,
                'httpMethod': 'GET'
            },
            'indexer': {
                'function': indexer_lambda,
                'httpMethod': 'POST'
            }
            }
