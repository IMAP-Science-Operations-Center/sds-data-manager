# TODO: add test to make certain Docker container is continuously running
# in the EC2 instance. This is the code that is used to test it manually:

import socket
import time

while True:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect(("your-ec2-instance-ip", 8080))
        s.sendall(b"Your test message")
    time.sleep(1)
