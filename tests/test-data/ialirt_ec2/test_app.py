"""A simple, dockerized, deployable Flask web application.

A simple Flask web application designed to be Dockerized and deployed on an
EC2 instance. Intended for verifying the successful deployment and operation in
an ECR and EC2 setup. The application listens on all interfaces (0.0.0.0) at
multiple ports, allowing external access for testing.
"""

import os
from flask import Flask
from multiprocessing import Process

# Function to create a Flask application for a specific port
def create_app(port):
    app = Flask(__name__)

    @app.route("/")
    def hello():
        """Hello world function to test with."""
        return f"Hello World from Port {port}."

    @app.route("/list")
    def list_files():
        """List files in the mounted S3 bucket."""
        files = os.listdir("/mnt/s3/packets")
        return "<br>".join(files)

    return app

# Function to create a test file for each port
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

# Function to run the Flask app on a specified port
def run_app(port):
    app = create_app(port)
    create_and_save_file(port)
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    # Define the ports on which to run the Flask instances
    ports = [1234, 1235]

    # Start a separate process for each Flask instance on each port
    processes = []
    for port in ports:
        process = Process(target=run_app, args=(port,))
        process.start()
        processes.append(process)

    # Wait for all processes to complete
    for process in processes:
        process.join()
