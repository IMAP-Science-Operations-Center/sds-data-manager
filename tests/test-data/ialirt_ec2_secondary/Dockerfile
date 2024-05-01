# This code is used to Dockerize test_app.py. The workflow is as follows:
# 1. Login to the ECR with `aws ecr get-login-password --region <region> | docker login
# --username AWS --password-stdin <ecr uri>`
# 2. Build the image with `docker build -t my-image-secondary --rm .`
# 3. docker tag my-image-secondary <ecr uri>:latest-secondary
# 4. docker push <ecr uri>:latest-secondary
FROM python:3.10
COPY . /app

# Set the working directory in the container
WORKDIR /app

# Install Flask
RUN pip install flask

# Make port 80 available to the world outside this container
EXPOSE 80

# Run test_app.py when the container launches
CMD ["python", "test_app.py"]
