"""A simple, dockerized, deployable Flask web application.

A simple Flask web application designed to be Dockerized and deployed on an
EC2 instance. Intended for verifying the successful deployment and operation in
an ECR and EC2 setup. The application listens on all interfaces (0.0.0.0) at
defined ports, allowing external access for testing.
"""

import multiprocessing
import os

import requests
from flask import Flask


def create_app(port):
    """Create Flask application for a specific port."""
    app = Flask(__name__)

    # Decorator that tells Flask what URL
    # should trigger the function that follows.
    @app.route("/")
    def hello():
        """Hello world function to test with."""
        return f"Hello from Port {port}!"

    @app.route("/list")
    def list_files():
        """List files in the mounted S3 bucket."""
        files = os.listdir("/mnt/s3/packets")
        return "<br>".join(files)

    @app.route("/test")
    def outbound_test():
        """Test outbound connectivity by making an HTTP request."""
        try:
            response = requests.get("https://api.ipify.org?format=json", timeout=5)
            return (
                f"Port {port}: Outbound request successful! "
                f"Public IP: {response.json()['ip']}"
            )
        except requests.RequestException as e:
            return f"Port {port}: Outbound request failed! Error: {e!s}", 500

    app.run(host="0.0.0.0", port=port)


def create_and_save_file(port):
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
    ports = [7526, 7560, 7564, 7566, 7568]
    for port in ports:
        create_and_save_file(port)
    processes = [
        multiprocessing.Process(target=create_app, args=(port,)) for port in ports
    ]

    for process in processes:
        process.start()

    for process in processes:
        process.join()
