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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
