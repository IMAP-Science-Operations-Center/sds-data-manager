# This code is used to Dockerize test_app.py. The workflow is as follows:
# 1. Login to the ECR with `aws ecr get-login-password --region <region> | docker login
# --username AWS --password-stdin <ecr uri>`
# 2. Build the image with `docker build -t my-image-<primary or secondary> --rm .`
# 3. docker tag my-image-<primary or secondary> <ecr uri>:latest-<primary or secondary>
# 4. docker push <ecr uri>:latest-<primary or secondary>

# To run and test locally:
# 1. `docker build -t my-image-<primary or secondary> --rm .`
# 2. `docker run -it --privileged -e AWS_PROFILE=<profile> -v /dev/macfuse0:/dev/macfuse0
# -v ~/.aws:/root/.aws -p <port>:<port> my-image-<primary or secondary>`
# 3. http://localhost:<port> to see the "Hello World." message.
# http://localhost:<port>/list to see the list of files in the mounted S3 bucket.

FROM python:3.10
COPY . /app

# Set the working directory in the container
WORKDIR /app

# Install Flask
RUN pip install flask

# Non-interactive frontend for apt-get
ARG DEBIAN_FRONTEND=noninteractive

# Install dependencies and mountpoint-s3
RUN apt-get update && \
    apt-get install -y sudo wget fuse apt-utils && \
    wget https://s3.amazonaws.com/mountpoint-s3-release/latest/x86_64/mount-s3.deb && \
    sudo apt-get install -y ./mount-s3.deb && \
    rm ./mount-s3.deb && \
    # Create the /mnt/s3 directory
    mkdir -p /mnt/s3

# Copy the scripts into the container
COPY mount_s3.sh /app/mount_s3.sh
COPY start_flask.sh /app/start_flask.sh
COPY entrypoint.sh /app/entrypoint.sh

# Make the scripts executable
RUN chmod +x /app/mount_s3.sh /app/start_flask.sh /app/entrypoint.sh

# Make port 8080 available to the world outside this container
# Note: The port number is changed from 8080 to 80 for the secondary system.
EXPOSE 80

# Set the AWS region
ENV AWS_REGION us-west-2

# Command to run the entrypoint script
CMD ["/app/entrypoint.sh"]
