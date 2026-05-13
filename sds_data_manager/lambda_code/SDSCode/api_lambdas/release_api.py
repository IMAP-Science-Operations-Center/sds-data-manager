"""Lambda function for release API endpoint."""


def lambda_handler(event, context):
    """Lambda handler for release API."""
    return {"statusCode": 200, "body": "Release API is working!"}
