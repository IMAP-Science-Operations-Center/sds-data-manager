"""Authorization for API Keys within the SDS."""

import logging

import boto3

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize DynamoDB resource
# Specifically outside of the handler to be cached in the lambda execution environment
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("imap-sdc-api-keys")


def _is_authorized(scope, path, http_method):
    """Check if the API key is authorized for the requested operation.

    Parameters
    ----------
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
    logger.info(
        f"Checking authorization - scope: {scope}, path: {path}, method: {http_method}"
    )

    # Restrict write operations for read scope
    if scope == "read" and http_method in ("PUT", "POST", "DELETE", "PATCH"):
        logger.warning(
            f"DENIED: read scope user attempted {http_method} operation on {path}"
        )
        return False

    # Restrict write operations (upload) for read scope
    if scope == "read" and path.startswith("/api-key/upload"):
        logger.warning(f"DENIED: read scope user attempted upload on {path}")
        return False

    # Public download except for logs and packets.
    if (
        path.startswith("/ialirt-download/logs")
        or path.startswith("/ialirt-download/packets/")
        or path.startswith("/space-weather-priority")
    ) and scope not in (
        "full",
        "read",
    ):
        logger.warning(
            f"DENIED: scope '{scope}' not authorized for I-ALiRT download endpoint"
        )
        return False

    logger.info(f"AUTHORIZED: API key with scope '{scope}' granted access to {path}")
    return True


def lambda_handler(event, context):
    """Get the API Key from the request header and check if it is valid."""
    logger.info(f"Received authorization request with event: {event}")
    api_key = event.get("headers", {}).get("x-api-key", None)

    if not api_key:
        logger.warning("DENIED: No API key provided in request headers")
        return {"isAuthorized": False}

    logger.info("API key received. Checking authorization...")

    # Retrieve metadata from DynamoDB
    try:
        metadata = table.get_item(Key={"api_key": api_key}).get("Item")
    except Exception as e:
        logger.error(f"Error retrieving API key metadata from DynamoDB: {e}")
        return {"isAuthorized": False}

    if not metadata:
        logger.warning("DENIED: API key not found in database")
        return {"isAuthorized": False}

    scope = metadata.get("scope", "")
    path = event.get("rawPath") or event.get("path", "")
    http_method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    logger.info(f"API key found with scope: {scope}")
    logger.info(f"Request details - Path: {path}, Method: {http_method}")

    is_authorized = _is_authorized(scope, path, http_method)
    if not is_authorized:
        logger.warning(
            f"DENIED: API key with scope '{scope}' is not authorized to upload file"
        )
        return {
            "isAuthorized": False,
            "context": {
                "apiKey": api_key,
                "scope": scope,
            },
        }

    logger.info(f"Authorization successful for scope '{scope}'")
    return {
        "isAuthorized": is_authorized,
        "context": {
            "apiKey": api_key,
            "scope": scope,
        },
    }
