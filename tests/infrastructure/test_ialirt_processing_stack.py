# TODO: add test to make certain Docker container is continuously running
# in the EC2 instance. This is the code that is used to test it manually:

import os
import socket

import pytest

# Set environment variable first
# export EC2_IP_ADDRESS="your-ec2-instance-ip"
# pytest test_ec2_socket_connection.py from command line
# or set it in the IDE


# Environment variable for EC2 IP Address
EC2_IP = os.getenv("EC2_IP_ADDRESS")


def test_socket_connection_to_ec2():
    assert EC2_IP is not None, "EC2 IP Address is not set in environment variables."

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(60)  # Timeout for socket operations
        try:
            s.connect((EC2_IP, 8080))
            s.sendall(b"Your test message")
            # Optionally wait for response here if needed
            # response = s.recv(1024)
        except socket.timeout:
            pytest.fail("Socket connection timed out")
        except OSError as e:
            pytest.fail(f"Socket error occurred: {e}")
        finally:
            s.close()

    print("hi")

    # Optional: Assertions based on response
    # assert response == expected_response,
    # "Response from server didn't match expected response."


# while True:
#     with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
#         s.connect(("ip address", 8080))
#         s.sendall(b"Your test message")
#     time.sleep(60)
#
#
# s.connect(("ip-10-0-0-58.us-west-2.compute.internal", 8080))
