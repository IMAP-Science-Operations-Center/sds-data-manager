# Dockerfile that installs imap_processing and its dependencies
FROM public.ecr.aws/docker/library/python:3.10-slim
USER root

# Upgrade pip to the latest version
RUN pip install --upgrade pip

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y git
RUN pip install requests

# Create a directory for storing uploaded/downloaded files
RUN mkdir -p /mnt/data

# Set an environment variable to indicate the Docker environment
ENV DOCKER_ENV=true

# Define the entrypoint of the container
ENTRYPOINT ["imap_api"]
