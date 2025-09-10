"""API utils."""

import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def is_authenticated_path(event):
    """Check if the API path is authenticated, allowing access to unreleased files.

    This function examines the routeKey and rawPath in the event to determine
    if the request is coming through an authenticated path (containing 'api-key'
    or 'auth'). Authenticated paths have access to all files, while
    non-authenticated paths only have access to released files.

    Parameters
    ----------
    event : dict
        The API Gateway event object

    Returns
    -------
    bool
        True if the path is authenticated, False otherwise
    """
    # Get the routeKey and rawPath, defaulting to empty strings if not present
    auth_api_endpoint = ["api-key", "auth"]

    # Safely extract route_key and raw_path
    route_key = ""
    if event.get("routeKey") and "/" in event.get("routeKey"):
        route_key = event.get("routeKey").split("/")[1]

    raw_path = ""
    if (
        event.get("rawPath")
        and "/" in event.get("rawPath")
        and len(event.get("rawPath").split("/")) > 1
    ):
        raw_path = event.get("rawPath").split("/")[1]

    logger.info(f"Route Key: {route_key}, Raw Path: {raw_path}")
    # Check if either contains authentication indicators
    is_auth_user = route_key in auth_api_endpoint or raw_path in auth_api_endpoint

    # Log the authentication determination
    if is_auth_user:
        logger.info("Authenticated path detected")
    else:
        logger.info("Non-authenticated path detected")

    return is_auth_user


def filter_files(event, search_results):
    """Filter files based on authentication status.

    This function filters the search results to only include released files
    for non-authenticated users.

    Parameters
    ----------
    event : dict
        The API Gateway event object. Cropped event example:
        {
            "version": "2.0",
            "routeKey": "GET /api-key/query",
            "rawPath": "/api-key/query",
            "rawQueryString": "table=science&instrument=swe",
            "queryStringParameters": {
                "instrument": "hit",
                "table": "science"
            },
            "isBase64Encoded": False
        }
    search_results : list
        The search results from the database query

    Returns
    -------
    list
        Filtered search results
    """
    is_auth_user = is_authenticated_path(event)

    # Log the authentication determination
    if is_auth_user:
        logger.info("Returning all files, including unreleased ones.")
        return search_results

    # Filter out unreleased files for non-authenticated paths
    released_files = [
        result for result in search_results if result.get("released") is True
    ]
    logger.info("Returning only released files.")
    return released_files
