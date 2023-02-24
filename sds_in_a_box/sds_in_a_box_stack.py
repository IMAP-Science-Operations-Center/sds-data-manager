import os
import string
import random
from aws_cdk import (
    # Duration,
    Stack,
    RemovalPolicy,
    aws_lambda_python_alpha
)
from constructs import Construct
import aws_cdk as cdk
import aws_cdk.aws_s3 as s3
import aws_cdk.aws_lambda as lambda_
import aws_cdk.aws_lambda_python_alpha as lambda_alpha_
import aws_cdk.aws_iam as iam
import aws_cdk.aws_opensearchservice as opensearch
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_secretsmanager as secretsmanager
import aws_cdk.aws_cognito as cognito
from aws_cdk.aws_lambda_event_sources import S3EventSource, SnsEventSource

class SdsInABoxStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, SDS_ID: str, initial_email: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
########### INIT
        initial_user_context = initial_email

########### DATA STORAGE 
        # This is the S3 bucket where the data will be stored
        data_bucket = s3.Bucket(self, "DATA-BUCKET",
                                bucket_name=f"sds-data-{SDS_ID}",
                                versioned=True,
                                removal_policy=RemovalPolicy.DESTROY,
                                auto_delete_objects=True
                                )

########### DATABASE
        # Need to make a secret username/password for OpenSearch
        os_secret = secretsmanager.Secret(self, "OpenSearchPassword")

        # Create the opensearch cluster
        sds_metadata_domain = opensearch.Domain(
            self,
            "SDSMetadataDomain",
            # Version 1.3 released 07/27/22
            version=opensearch.EngineVersion.OPENSEARCH_1_3,
            # Define the Nodes
            # Supported EC2 instance types:
            # https://docs.aws.amazon.com/opensearch-service/latest/developerguide/supported-instance-types.html
            capacity=opensearch.CapacityConfig(
                # Single node for DEV
                data_nodes=1,
                data_node_instance_type="t3.small.search"
            ),
            # 10GB standard SSD storage, 10GB is the minimum size
            ebs=opensearch.EbsOptions(
                volume_size=10,
                volume_type=ec2.EbsDeviceVolumeType.GP2,
            ),
            # Enable logging
            logging=opensearch.LoggingOptions(
                slow_search_log_enabled=True,
                app_log_enabled=True,
                slow_index_log_enabled=True,
            ),
            # Enable encryption
            node_to_node_encryption=True,
            encryption_at_rest=opensearch.EncryptionAtRestOptions(enabled=True),
            # Require https connections
            enforce_https=True,
            # Destroy OS with cdk destroy
            removal_policy=RemovalPolicy.DESTROY,
            fine_grained_access_control=opensearch.AdvancedSecurityOptions(
              master_user_name="master-user",
              master_user_password=os_secret.secret_value
            )
        )

        # add an access policy for opensearch
        sds_metadata_domain.add_access_policies(
            iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            principals=[
                iam.AnyPrincipal()
            ],
            actions=["es:*"],
            resources=[sds_metadata_domain.domain_arn + "/*"]
            ))

########### COGNITO
        # Create the Cognito UserPool
        userpool = cognito.UserPool(self,
                                    id='TeamUserPool',
                                    account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
                                    auto_verify=cognito.AutoVerifiedAttrs(email=True),
                                    standard_attributes=cognito.
                                    StandardAttributes(email=cognito.StandardAttribute(required=True)),
                                    sign_in_aliases=cognito.SignInAliases(username=False, email=True),
                                    removal_policy=cdk.RemovalPolicy.DESTROY
                                    )

        # Add a client sign in for the userpool
        command_line_client = cognito.UserPoolClient(user_pool=userpool, scope=self, id='sds-command-line',
                                                     user_pool_client_name= f"sdscommandline-{SDS_ID}",
                                                     id_token_validity=cdk.Duration.minutes(60),
                                                     access_token_validity=cdk.Duration.minutes(60),
                                                     refresh_token_validity=cdk.Duration.minutes(60),
                                                     auth_flows=cognito.AuthFlow(admin_user_password=True,
                                                                                 user_password=True,
                                                                                 user_srp=True,
                                                                                 custom=True),
                                                     prevent_user_existence_errors=True)
        
        # Add a random unique domain name where users can sign up / reset passwords
        # Users will be able to reset their passwords at https://sds-login-{SDS_ID}.auth.us-west-2.amazoncognito.com/login?client_id={}&redirect_uri=https://example.com&response_type=code
        userpooldomain = userpool.add_domain(id="TeamLoginCognitoDomain",
                                             cognito_domain=cognito.CognitoDomainOptions(domain_prefix=f"sds-login-{SDS_ID}"))

        # Add a lambda function that will trigger whenever an email is sent to the user (see the lambda section above)

        # Create an initial user of the API
        initial_user = cognito.CfnUserPoolUser(self, "MyCfnUserPoolUser",
                                               user_pool_id=userpool.user_pool_id,
                                               desired_delivery_mediums=["EMAIL"],
                                               force_alias_creation=False,
                                               user_attributes=[cognito.CfnUserPoolUser.AttributeTypeProperty(
                                                  name="email",
                                                  value=initial_user_context
                                               )],
                                               username=initial_user_context
                                              )

########### IAM POLICIES
        opensearch_all_http_permissions = iam.PolicyStatement(
                                               effect=iam.Effect.ALLOW,
                                               actions=["es:ESHttp*"],
                                               resources=[f"{sds_metadata_domain.domain_arn}/*"],
                                          )
        opensearch_read_only_policy = iam.PolicyStatement(
                                             effect=iam.Effect.ALLOW,
                                             actions=["es:ESHttpGet"],
                                             resources=[f"{sds_metadata_domain.domain_arn}/*"],
                                      )
        s3_write_policy = iam.PolicyStatement(
                              effect=iam.Effect.ALLOW,
                              actions=["s3:PutObject"],
                              resources=[
                                  f"{data_bucket.bucket_arn}/*"
                              ],
                          )
        s3_read_policy = iam.PolicyStatement(
                              effect=iam.Effect.ALLOW,
                              actions=["s3:GetObject"],
                              resources=[
                                  f"{data_bucket.bucket_arn}/*"
                              ],
                          )
        
        cognito_admin_policy = iam.PolicyStatement(
                                    effect=iam.Effect.ALLOW,
                                    actions=["cognito-idp:*"],
                                    resources=[
                                        f"*"
                                    ],
                                )

########### LAMBDA FUNCTIONS
        
        # The purpose of this lambda function is to trigger off of a new file entering the SDC.
        indexer_lambda = lambda_alpha_.PythonFunction(self,
                                          id="IndexerLambda",
                                          function_name=f'file-indexer-{SDS_ID}',
                                          entry=os.path.join(os.path.dirname(os.path.realpath(__file__)), "SDSCode"),
                                          index = "indexer.py",
                                          handler="lambda_handler",
                                          runtime=lambda_.Runtime.PYTHON_3_9,
                                          timeout=cdk.Duration.minutes(15),
                                          memory_size=1000,
                                          environment={
                                            "OS_ADMIN_USERNAME": "master-user", 
                                            "OS_ADMIN_PASSWORD_LOCATION": os_secret.secret_value.unsafe_unwrap(),
                                            "OS_DOMAIN": sds_metadata_domain.domain_endpoint,
                                            "OS_PORT": "443",
                                            "OS_INDEX": "metadata",
                                            "S3_BUCKET": data_bucket.s3_url_for_object()}
                                          )
        indexer_lambda.add_event_source(S3EventSource(data_bucket,
                                                      events=[s3.EventType.OBJECT_CREATED]
                                                      )
                                        )
        indexer_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
        
        # Adding Opensearch permissions 
        indexer_lambda.add_to_role_policy(opensearch_all_http_permissions)        

        # Adding a lambda for uploading files to the SDS
        upload_api_lambda = lambda_alpha_.PythonFunction(self,
                                      id="UploadAPILambda",
                                      function_name=f'upload-api-handler-{SDS_ID}',
                                      entry=os.path.join(os.path.dirname(os.path.realpath(__file__)), "SDSCode/"),
                                      index="upload_api.py",
                                      handler="lambda_handler",
                                      runtime=lambda_.Runtime.PYTHON_3_9,
                                      timeout=cdk.Duration.minutes(15),
                                      memory_size=1000,
                                      environment={"COGNITO_USERPOOL_ID": userpool.user_pool_id, 
                                                   "COGNITO_APP_ID": command_line_client.user_pool_client_id,
                                                   "S3_BUCKET": data_bucket.s3_url_for_object()},
        )
        upload_api_lambda.add_to_role_policy(s3_write_policy)
        upload_api_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
        upload_api_url = upload_api_lambda.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE,
                                              cors=lambda_.FunctionUrlCorsOptions(allowed_origins=["*"]))

        # The purpose of this lambda function is to trigger off of a lambda URL.
        query_api_lambda = lambda_alpha_.PythonFunction(self,
                                          id="QueryAPILambda",
                                          function_name=f'query-api-handler-{SDS_ID}',
                                          entry=os.path.join(os.path.dirname(os.path.realpath(__file__)), "SDSCode/"),
                                          index="queries.py",
                                          handler="lambda_handler",
                                          runtime=lambda_.Runtime.PYTHON_3_9,
                                          timeout=cdk.Duration.minutes(1),
                                          memory_size=1000,
                                          environment={
                                            "COGNITO_USERPOOL_ID": userpool.user_pool_id, 
                                            "COGNITO_APP_ID": command_line_client.user_pool_client_id,
                                            "OS_ADMIN_USERNAME": "master-user", 
                                            "OS_ADMIN_PASSWORD_LOCATION": os_secret.secret_value.unsafe_unwrap(),
                                            "OS_DOMAIN": sds_metadata_domain.domain_endpoint,
                                            "OS_PORT": "443",
                                            "OS_INDEX": "metadata"
                                            }
                                          )
        query_api_lambda.add_to_role_policy(opensearch_read_only_policy)

        # add function url for lambda query API
        lambda_query_api_function_url = lambda_.FunctionUrl(self,
                                                 id="QueryAPI",
                                                 function=query_api_lambda,
                                                 auth_type=lambda_.FunctionUrlAuthType.NONE,
                                                 cors=lambda_.FunctionUrlCorsOptions(
                                                                     allowed_origins=["*"],
                                                                     allowed_methods=[lambda_.HttpMethod.GET]))
        # download query API lambda
        download_query_api = lambda_alpha_.PythonFunction(self,
            id="DownloadQueryAPILambda",
            function_name=f'download-query-api-{SDS_ID}',
            entry=os.path.join(os.path.dirname(os.path.realpath(__file__)), "SDSCode/"),
            index='download_query_api.py',
            handler="lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            timeout=cdk.Duration.seconds(60),
            environment={"COGNITO_USERPOOL_ID": userpool.user_pool_id, 
                         "COGNITO_APP_ID": command_line_client.user_pool_client_id)
           )
        download_query_api.add_to_role_policy(opensearch_all_http_permissions)
        download_query_api.add_to_role_policy(s3_read_policy)
        
        # Adding a function URL
        download_api_url = lambda_.FunctionUrl(self,
                                               id="DownloadQueryAPI",
                                               function=download_query_api,
                                               auth_type=lambda_.FunctionUrlAuthType.NONE,
                                               cors=lambda_.FunctionUrlCorsOptions(
                                                                   allowed_origins=["*"],
                                                                   allowed_methods=[lambda_.HttpMethod.GET])
        )
    
        # Adding a lambda that sends out an email with a link where the user can reset their password
        signup_lambda = lambda_alpha_.PythonFunction(self,
                                         id="SignupLambda",
                                         function_name=f'cognito_signup_message-{SDS_ID}',
                                         entry=os.path.join(os.path.dirname(os.path.realpath(__file__)), "SDSCode/"),
                                         index="cognito_signup_message.py",
                                         handler="lambda_handler",
                                         runtime=lambda_.Runtime.PYTHON_3_9,
                                         timeout=cdk.Duration.minutes(15),
                                         memory_size=1000,
                                         environment={"COGNITO_DOMAIN_PREFIX": f"sds-login-{SDS_ID}", 
                                                      "COGNITO_DOMAIN": f"https://sds-login-{SDS_ID}.auth.us-west-2.amazoncognito.com", 
                                                      "SDS_ID": SDS_ID}
        )
        signup_lambda.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
        # Adding Cognito Permissions
        signup_lambda.add_to_role_policy(cognito_admin_policy)

        userpool.add_trigger(cognito.UserPoolOperation.CUSTOM_MESSAGE, signup_lambda)


########### OUTPUTS
        # This is a list of the major outputs of the stack
        cdk.CfnOutput(self, "UPLOAD_API_URL", value=upload_api_url.url)
        cdk.CfnOutput(self, "QUERY_API_URL", value=lambda_query_api_function_url.url)
        cdk.CfnOutput(self, "DOWNLOAD_API_URL", value=download_api_url.url)
        cdk.CfnOutput(self, "COGNITO_USERPOOL_ID", value=userpool.user_pool_id)
        cdk.CfnOutput(self, "COGNITO_APP_ID", value=command_line_client.user_pool_client_id)
        cdk.CfnOutput(self, "SIGN_IN_WEBPAGE", value=f"https://sds-login-{SDS_ID}.auth.us-west-2.amazoncognito.com/login?client_id={command_line_client.user_pool_client_id}&redirect_uri=https://example.com&response_type=code")