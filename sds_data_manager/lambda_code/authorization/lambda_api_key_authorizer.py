"""Authorization for API Keys within the SDS."""

import boto3

# Initialize DynamoDB resource
# Specifically outside of the handler to be cached in the lambda execution environment
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("imap-sdc-api-keys")


def lambda_handler(event, context):
    """Get the API Key from the request header and check if it is valid."""
    api_key = event.get("headers", {}).get("x-api-key", None)

    if not api_key:
        return {"isAuthorized": False}

    # Retrieve metadata from DynamoDB
    try:
        metadata = table.get_item(Key={"api_key": api_key}).get("Item")
    except Exception:
        # Log? print(f"Error retrieving API key metadata: {e}")
        return {"isAuthorized": False}
    if not metadata:
        return {"isAuthorized": False}

    scope = metadata.get("scope", "")
    path = event.get("rawPath") or event.get("path", "")

    # Check scope-based authorization for specific endpoints
    if path.startswith("/ialirt-db-query") and scope not in (
        "ialirt_db",
        "full",
        "ialirt_external_partner",
        "ialirt_scientist",
    ):
        return {"isAuthorized": False}

    if path.startswith("/ialirt-download") and scope not in (
        "full",
        "ialirt_external_partner",
        "ialirt_scientist",
    ):
        return {"isAuthorized": False}

    return {
        "isAuthorized": True,
        "context": {
            "apiKey": api_key,
            "scope": scope,
        },
    }
