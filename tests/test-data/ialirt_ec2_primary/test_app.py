"""Test the i-alirt processing stack."""

import os

import boto3
from flask import Flask

app = Flask(__name__)

# Configure the S3 client
s3 = boto3.client("s3")

# Set the upload folder and allowed extensions
UPLOAD_FOLDER = "/mnt/s3"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Define the bucket name and file details
sts_client = boto3.client("sts")
identity = sts_client.get_caller_identity()
account = identity["Account"]
bucket_name = f"sds-data-{account}"
file_name = "test_file_primary.txt"
local_file_path = os.path.join(UPLOAD_FOLDER, file_name)


def create_and_upload_file():
    """Create and upload a file."""
    # TODO: this will be replaced by an upload api
    os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
    with open(local_file_path, "w") as file:
        file.write("Hello, this is a test file.")

    s3.upload_file(local_file_path, bucket_name, file_name)
    print(
        f"File {file_name} created and uploaded to S3 bucket "
        f"{bucket_name} successfully."
    )


@app.route("/")
def hello():
    """Hello world function to test with."""
    return "Hello, World!"


@app.route("/list")
def list_files():
    """List files in the mounted S3 bucket."""
    files = os.listdir("/mnt/s3")
    return "<br>".join(files)


if __name__ == "__main__":
    create_and_upload_file()
    app.run(host="0.0.0.0", port=8080)
