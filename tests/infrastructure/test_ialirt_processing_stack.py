"""
Verify the successful deployment and operation in an
ECR and EC2 setup.

Note: verification may also be done via the
webbrowser: http://<EC2_IP>:8080/
"""
import os

import pytest
import requests

# Environment variable for EC2 IP Address (set manually)
EC2_IP = os.getenv("EC2_IP_ADDRESS")


def test_flask_app_response():
    """Test the Flask application response."""
    assert EC2_IP is not None, "EC2 IP Address is not set in environment variables."

    url = f"http://{EC2_IP}:8080/"
    try:
        response = requests.get(url, timeout=60)
        # Check if the response content is "Hello World!"
        assert response.text == "Hello World!"
    except requests.exceptions.ConnectionError:
        pytest.fail("Failed to connect to the Flask application.")
    except requests.exceptions.Timeout:
        pytest.fail("Connection to the Flask application timed out.")
