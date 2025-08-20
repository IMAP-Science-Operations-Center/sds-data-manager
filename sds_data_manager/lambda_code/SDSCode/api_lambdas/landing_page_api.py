"""Define lambda to support the home page reroute of the API."""


def lambda_handler(event, context):
    """Entry point to the landing page redirect lambda.

    Redirects incoming requests to the root API path ("/")
    and sends them to the external landing page URL via HTTP 302 redirect.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide information
        about the invocation, function, and runtime environment.

    Returns
    -------
    dict
        A dictionary containing the HTTP status code (302), headers with the
        Location field set to the landing page URL, and no body
    """
    return {
        "statusCode": 302,
        "headers": {
            "Location": "https://imap-processing.readthedocs.io/en/latest/data-access/index.html"
        },
        "body": "",
    }
