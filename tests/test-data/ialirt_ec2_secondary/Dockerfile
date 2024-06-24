# This code is used to Dockerize test_app.py. The workflow is as follows:
# 1. Login to the ECR with `aws ecr get-login-password --region <region> | docker login
# --username AWS --password-stdin <ecr uri>`
# 2. Build the image with `docker build -t my-image-secondary --rm .`
# 3. docker tag my-image-secondary <ecr uri>:latest-secondary
# 4. docker push <ecr uri>:latest-secondary

# To run and test locally:
# 1. `docker build -t my-image-secondary --rm .`
# 2. `docker run -it --privileged -e AWS_PROFILE=<profile> -v /dev/macfuse0:/dev/macfuse0
# -v ~/.aws:/root/.aws -p 8080:8080 my-image-secondary`
# 3. http://localhost:8080 to see the "Hello World Secondary." message.
# http://localhost:8080/list to see the list of files in the mounted S3 bucket.

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
    rm ./mount-s3.deb

# Create the /mnt/s3 directory
RUN mkdir -p /mnt/s3

# Make port 80 available to the world outside this container
EXPOSE 80

# Set the AWS region
ENV AWS_REGION us-west-2

# Command to mount S3 and run the application sequentially
CMD ["sh", "-c", "mount-s3 sds-data-301233867300 /mnt/s3 && python test_app.py"]
