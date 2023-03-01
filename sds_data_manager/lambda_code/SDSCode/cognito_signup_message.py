"""
This function triggers as a response to ALL emails sent by the Cognito Userpool, 
set up by the CDK.  

The job of this function is to take the input "event" dictionary, modify it, 
and then return the modified event dictionary back to cognito.  

More information can be found here: 
https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-lambda-custom-message.html
"""

import os
import boto3 

def lambda_handler(event, context):
    '''
    We expect the "event" variable to look like the following: 

    {
        "version": 1,
        "triggerSource": "CustomMessage_AdminCreateUser",
        "region": "<region>",
        "userPoolId": "<userPoolId>",
        "userName": "<userName>",
        "callerContext": {
            "awsSdk": "<calling aws sdk with version>",
            "clientId": "<apps client id>",
            ...
        },
        "request": {
            "userAttributes": {
                "phone_number_verified": false,
                "email_verified": true,
                ...
            },
            "codeParameter": "####",
            "usernameParameter": "username"
        },
        "response": {
            "smsMessage": "<custom message to be sent in the message 
                            with code parameter and username parameter>"
            "emailMessage": "<custom message to be sent in the message 
                              with code parameter and username parameter>"
            "emailSubject": "<custom email subject>"
        }
    }
    '''

    client = boto3.client('cognito-idp')

    # These environment variables should have been set up by the CDK application 
    domain_desc = client.describe_user_pool_domain(
                         Domain=os.environ["COGNITO_DOMAIN_PREFIX"]
    )
    clients = client.list_user_pool_clients(
                     UserPoolId = domain_desc["DomainDescription"]['UserPoolId']
    )
    command_line_client = clients['UserPoolClients'][0]['ClientId']
    
    if 'triggerSource' in event:
        if event['triggerSource'] == 'CustomMessage_AdminCreateUser':
            # Modify the email a new user receives to contain a 
            # link to the cognito domain.
            subject = f"Science Data System {os.environ['SDS_ID']} sign up"            
            event["response"]["emailSubject"] = subject
            event["response"]["smsMessage"] = "We don't use this"
            message = "Your Username is {username} and your password is {####}." +  
                      "Continue signing up at: " + 
                      os.environ["COGNITO_DOMAIN"] + "/login?client_id="+ 
                      command_line_client + 
                      "&redirect_uri=https://example.com&response_type=code"
            event["response"]["emailMessage"]= message
                            
            print(event)
    return event