"""API utils."""

import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def is_authenticated_user(event):
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
    rk = event.get("routeKey", "")
    route_key = rk.split("/")[1] if "/" in rk else ""
    return route_key in ("authorized", "api-key")
