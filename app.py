#!/usr/bin/env python3
'''
This stack generates the necessary AWS services for the data management 
portions of a Science Data System.  

Options can be included by adding "--context param=value" to a cdk s
ynth/deploy command, for example:

cdk deploy --context SDSID=harter-testing \
           --context userpool_name=sds-userpool-dev \
           --context app_client_name=sdscommandline-dev \
           --all

:context SDSID: Required. This is a unique identifier for your stack.  
:context initial_user: Optional. This is the email address of the initial
                       cognito user.  If not provided, the first user will 
                       need to be set up in the AWS console.   
:context userpool_name: Optional (Required if app_client_name given). 
                        This is the name of the userpool that contains 
                        the specified app_client_name.
:context app_client_name: Optional (Required if userpool_name given). 
                          This is the name of the app client on the AWS 
                          account that will be used to verify received 
                          tokens in the API.  
:context cognito_only: Optional.  If true, then this stack will only 
                       deploy cognito services.  

To summarize, there are 3 "modes" for this application:

1) Cognito is spun up at the same time as the rest of the stack.  
   Context needed: None  
2) No Cognito services are created and a pre-existing cognito userpool is used.  
   Context needed: "userpool_name" and "app_client_name".  
3) ONLY Cognito services are created.  
   Context needed: "cognito_only".   

'''
import random
import string
import boto3
import aws_cdk as cdk
from sds_data_manager.sds_cognito_stack import SdsCognitoStack
from sds_data_manager.sds_data_manager_stack import SdsDataManagerStack

app = cdk.App()

# Grab context from cdk synth and cdk deploy commands
SDS_ID = app.node.try_get_context("SDSID")
initial_user = app.node.try_get_context("initial_user")
userpool_name = app.node.try_get_context("userpool_name")
app_client_name = app.node.try_get_context("app_client_name")
cognito_only = app.node.try_get_context("cognito_only")

if SDS_ID is None:
    raise ValueError(
        "ERROR: Need to specify an ID to name the stack (ex - production, testing, etc)"
    )
elif SDS_ID == "random":
    # A random unique ID for this particular instance of the SDS
    SDS_ID = "".join([random.choice(string.ascii_lowercase) for i in range(8)])

# We'll try to find the cognito userpool id and app client ID from the names
if userpool_name and app_client_name:
    cognito_client = boto3.client('cognito-idp')
    userpool_list = cognito_client.list_user_pools(MaxResults=60)
    for up in userpool_list['UserPools']:
        if up['Name'] == userpool_name:
            userpool_id = up['Id']
            break
    else:
        raise ValueError(f"There is no userpool with the name {userpool_name}.")
    app_client_list = cognito_client.list_user_pool_clients(UserPoolId=userpool_id, MaxResults=60)
    for ac in app_client_list['UserPoolClients']:
        if ac['ClientName'] == app_client_name:
            app_client_id = ac['ClientId']
            break
    else:
        raise ValueError(f"There is no appclient with the name {app_client_name}")
    create_cognito = False
elif userpool_name or app_client_name:
    raise Exception("Required to either specify both a Cognito Userpool and Appclient name.")
else:
    create_cognito = True

# If the criteria are met, create a brand new Cognito userpool to verify API access
if create_cognito:
    cognito_stack = SdsCognitoStack(app, f"SDSCognitoStack-{SDS_ID}", 
                                    sds_id=SDS_ID, initial_email=initial_user)
    userpool_id = cognito_stack.userpool_id
    app_client_id = cognito_stack.app_client_id

if not cognito_only:
    SdsDataManagerStack(app, f"SdsDataManager-{SDS_ID}", sds_id=SDS_ID, 
                        userpool_id=userpool_id, app_client_id=app_client_id)
app.synth()
