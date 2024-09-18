"""A simple, dockerized, deployable Flask web application.

A simple Flask web application designed to be Dockerized and deployed on an
EC2 instance. Intended for verifying the successful deployment and operation in
an ECR and EC2 setup. The application listens on all interfaces (0.0.0.0) at
port 8080, allowing external access for testing.
"""

import os

from flask import Flask

# Create a Flask application
app = Flask(__name__)
# Note: The port number is changed from 8080 to 80 in the secondary Dockerfile.
port = 80


# Decorator that tells Flask what URL
# should trigger the function that follows.
@app.route("/")
def hello():
    """Hello world function to test with."""
    return f"Hello World from Port {port}."


@app.route("/list")
def list_files():
    """List files in the mounted S3 bucket."""
    files = os.listdir("/mnt/s3/packets")
    return "<br>".join(files)


def create_and_save_file():
    """Create and save file to S3 bucket."""
    s3_mount_dir = "/mnt/s3/packets"

    if not os.path.exists(s3_mount_dir):
        os.makedirs(s3_mount_dir)

    file_name = f"test_file{port}.txt"
    file_content = "Hello, this is a test file."

    file_path = os.path.join(s3_mount_dir, file_name)

    with open(file_path, "w") as file:
        file.write(file_content)

    print(f"File {file_name} created and saved to {file_path}.")


if __name__ == "__main__":
    create_and_save_file()
    app.run(host="0.0.0.0", port=port)
