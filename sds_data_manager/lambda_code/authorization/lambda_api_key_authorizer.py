"""Authorization for API Keys within the SDS."""

import boto3

# Initialize DynamoDB resource
# Specifically outside of the handler to be cached in the lambda execution environment
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("imap-sdc-api-keys")


def _is_authorized(api_key, scope, path, http_method):
    """Check if the API key is authorized for the requested operation.

    Parameters
    ----------
    api_key : str
        The API key from the request
    scope : str
        The scope/permission level of the API key
    path : str
        The request path
    http_method : str
        The HTTP method (GET, POST, PUT, etc.)

    Returns
    -------
    bool
        True if authorized, False otherwise
    """
    # Restrict write operations for read-only scope
    if scope == "read" and http_method in ("PUT", "POST", "DELETE", "PATCH"):
        return False

    # Restrict write operations (upload) for read-only scope
    if scope == "read" and path.startswith("/api-key/upload"):
        return False

    # Check scope-based authorization for specific endpoints
    if path.startswith("/ialirt-db-query") and scope not in (
        "ialirt_db",
        "full",
        "ialirt_external_partner",
        "ialirt_scientist",
        "read",
    ):
        return False

    # Public download except for logs and packets.
    if (
        path.startswith("/ialirt-download/logs")
        or path.startswith("/ialirt-download/packets/")
    ) and scope not in (
        "full",
        "ialirt_external_partner",
        "ialirt_scientist",
        "read",
    ):
        return False

    return True


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
    http_method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    is_authorized = _is_authorized(api_key, scope, path, http_method)

    return {
        "isAuthorized": is_authorized,
        "context": {
            "apiKey": api_key,
            "scope": scope,
        },
    }
