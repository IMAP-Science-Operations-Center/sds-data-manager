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

class SdsCognitoStack(Stack):

    def __init__(self, scope: Construct, 
                 construct_id: str, 
                 SDS_ID: str, 
                 initial_email: str='',
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
########### INIT
        initial_user_context = initial_email

########### IAM POLICIES        
        cognito_admin_policy = iam.PolicyStatement(
                                    effect=iam.Effect.ALLOW,
                                    actions=["cognito-idp:*"],
                                    resources=[
                                        f"*"
                                    ],
                                )
                
########### COGNITO
        # Create the Cognito UserPool
        userpool = cognito.UserPool(self,
                                    id='TeamUserPool',
                                    user_pool_name=f'sds-userpool-{SDS_ID}',
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
        if initial_user_context:
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

########### LAMBDA
        # Adding a lambda that sends out an email with a link where the user can reset their password
        signup_lambda = lambda_alpha_.PythonFunction(self,
                                         id="SignupLambda",
                                         function_name=f'cognito_signup_message-{SDS_ID}',
                                         entry=os.path.join(os.path.dirname(os.path.realpath(__file__)), "lambda_code"),
                                         index="SDSCode/cognito_signup_message.py",
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

        self.userpool_id = userpool.user_pool_id
        self.app_client_id = command_line_client.user_pool_client_id

########### OUTPUTS
        cdk.CfnOutput(self, "COGNITO_USERPOOL_ID", value=userpool_id)
        cdk.CfnOutput(self, "COGNITO_APP_ID", value=app_client_id)
        cdk.CfnOutput(self, "SIGN_IN_WEBPAGE", value=f"https://sds-login-{SDS_ID}.auth.us-west-2.amazoncognito.com/login?client_id={app_client_id}&redirect_uri=https://example.com&response_type=code")