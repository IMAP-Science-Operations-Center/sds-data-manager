"""
Verify the successful deployment and operation in an
ECR and EC2 setup.

# TODO: this is not ideal, but it works until we setup ECS.
Steps for setting up test:
1. Deploy IalirtProcessing Stack
2. While in ialirt_ec2 directory build Docker image and
push to ECR. Follow the instructions in the Dockerfile.
3. Navigate to EC2 instance in the AWS Console, check
the box next to it and click Connect.
Connect using Session Manager.
4. Make certain you are logged in to the LASP VPN.
5. Run the following commands:
    a.  'sudo docker ps'
    b. if a container is not yet running:
    'sudo docker pull <repo uri>:latest'
    c. 'sudo docker run --rm -d -p 8080:8080 <repo uri>:latest'
6.  If you get a permissions error:
    'aws ecr get-login-password --region <region> |
    sudo docker login --username AWS --password-stdin <repo uri>'

Note: verification may also be done via the
webbrowser: http://<EC2_IP>:8080/
"""
import os

import requests

# Environment variable for EC2 IP Address (set manually)
EC2_IP = os.getenv("EC2_IP_ADDRESS")


def test_flask_app_response():
    """Test the Flask application response."""

    if EC2_IP is not None:  # pragma: no cover
        url = f"http://{EC2_IP}:8080/"
        requests.get(url, timeout=10)
