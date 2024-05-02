"""A simple, dockerized, deployable Flask web application.

A simple Flask web application designed to be Dockerized and deployed on an
EC2 instance. Intended for verifying the successful deployment and operation in
an ECR and EC2 setup. The application listens on all interfaces (0.0.0.0) at
port 8080, allowing external access for testing.
"""

from flask import Flask

# Create a Flask application
app = Flask(__name__)


# Decorator that tells Flask what URL
# should trigger the function that follows.
@app.route("/")
def hello():
    """Hello world function to test with."""
    return "Hello World."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
