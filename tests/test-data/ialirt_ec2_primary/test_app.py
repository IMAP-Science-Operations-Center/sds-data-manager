"""A simple, dockerized, deployable Flask web application.

A simple Flask web application designed to be Dockerized and deployed on an
EC2 instance. Intended for verifying the successful deployment and operation in
an ECR and EC2 setup. The application listens on all interfaces (0.0.0.0) at
port 8080, allowing external access for testing.
"""

import boto3
from flask import Flask, jsonify

# Create a Flask application
app = Flask(__name__)


# Decorator that tells Flask what URL
# should trigger the function that follows.
@app.route("/")
def hello():
    """Hello world function to test with."""
    return "Hola Mundo."


@app.route("/test-s3")
def test_s3():
    """Endpoint to test S3 put operation."""
    sts_client = boto3.client("sts")
    identity = sts_client.get_caller_identity()
    account = identity["Account"]
    bucket_name = f"sds-data-{account}"
    test_key = "test-object"

    try:
        s3_client = boto3.client("s3")
        s3_client.put_object(Bucket=bucket_name, Key=test_key, Body="This is a test.")
        return jsonify(
            {
                "message": "Successfully put object in S3 using Primary System",
                "bucket": bucket_name,
                "key": test_key,
            }
        )
    except Exception as e:
        return jsonify(
            {
                "message": "Failed to put object in S3 using Primary System",
                "error": str(e),
            }
        ), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
