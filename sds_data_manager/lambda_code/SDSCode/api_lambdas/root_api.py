"""Lambda for the root."""


def lambda_handler(event, context):
    """Entry point for the root path of the API Gateway.

    This function is triggered when a request is made to the base URL.
    Returns a simple JSON message indicating the API is live, along with a link
    to documentation or usage instructions.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    Returns
    -------
    dict
        A JSON response with the message.
    """
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": '{"message": "Welcome to the IMAP API. '
        'See [link to document for usage] for more information."}',
    }
