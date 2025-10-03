"""Authorization for API Keys within the SDS."""

import logging

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Initialize DynamoDB resource
TABLE_NAME = "imap-sdc-api-keys"


def get_api_key_metadata(api_key):
    """Retrieve API key metadata from DynamoDB."""
    try:
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(TABLE_NAME)
        response = table.get_item(Key={"api_key": api_key})
        return response.get("Item")
    except Exception as e:
        logger.error(f"Error retrieving API key metadata: {e}")
        return None


def lambda_handler(event, context):
    """Get the API Key from the request header and check if it is valid."""
    api_key = event.get("headers", {}).get("x-api-key", None)

    if not api_key:
        return {"isAuthorized": False}

    # Retrieve metadata from DynamoDB
    metadata = get_api_key_metadata(api_key)
    if not metadata:
        return {"isAuthorized": False}

    scope = metadata.get("scope", "")
    path = event.get("rawPath") or event.get("path", "")

    # Check scope-based authorization for specific endpoints
    if path.startswith("/ialirt-db-query") and scope not in ("ialirt_db", "full"):
        return {"isAuthorized": False}

    return {"isAuthorized": True, "context": {"apiKey": api_key}}
