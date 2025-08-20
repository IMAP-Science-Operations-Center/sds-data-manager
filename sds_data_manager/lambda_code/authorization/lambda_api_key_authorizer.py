"""Authorization for API Keys within the SDS."""

import json

import boto3

# Retrieve the API keys from AWS Systems Manager Parameter Store
# Do this outside of the lambda handler body to cache the keys
# to avoid unnecessary calls to SSM
ssm = boto3.client("ssm")
try:
    response = ssm.get_parameter(Name="imap-sdc-api-keys", WithDecryption=True)
    key_dict = json.loads(response["Parameter"]["Value"])
    valid_keys = set(key_dict.keys())
except Exception:
    # Could not load keys, deny access
    valid_keys = set()


def lambda_handler(event, context):
    """Get the API Key from the request header and check if it is valid."""
    api_key = event.get("headers", {}).get("x-api-key", None)

    if api_key and api_key in valid_keys:
        return {"isAuthorized": True, "context": {"apiKey": api_key}}
    else:
        return {"isAuthorized": False}
