"""Test the i-alirt processing stack."""

import os

from flask import Flask

app = Flask(__name__)


@app.route("/")
def hello():
    """Hello world function to test with."""
    return "Hello, World!"


@app.route("/list")
def list_files():
    """List files in the mounted S3 bucket."""
    files = os.listdir("/mnt/s3")
    return "<br>".join(files)


def create_and_save_file():
    """Create and save file to S3 bucket."""
    # Directory where the S3 bucket is mounted
    s3_mount_dir = "/mnt/s3"

    # Ensure the mount directory exists
    if not os.path.exists(s3_mount_dir):
        os.makedirs(s3_mount_dir)

    # File name and content
    file_name = "test_file.txt"
    file_content = "Hello, this is a test file."

    # Full path to the file in the mounted S3 directory
    file_path = os.path.join(s3_mount_dir, file_name)

    # Create and write to the file
    with open(file_path, "w") as file:
        file.write(file_content)

    print(f"File {file_name} created and saved to {file_path}.")


if __name__ == "__main__":
    create_and_save_file()
    app.run(host="0.0.0.0", port=8080)
