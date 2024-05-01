# This code is used to Dockerize test_app.py. The workflow is as follows:
# 1. Login to the ECR with `aws ecr get-login-password --region <region> | docker login
# --username AWS --password-stdin <ecr uri>`
# 2. Build the image with `docker build -t my-image-primary --rm .`
# 3. docker tag my-image-primary <ecr uri>:latest-primary
# 4. docker push <ecr uri>:latest-primary
FROM python:3.10
COPY . /app

# Set the working directory in the container
WORKDIR /app

# Install Flask
RUN pip install flask

# Make port 8080 available to the world outside this container
EXPOSE 8080

# Run test_app.py when the container launches
CMD ["python", "test_app.py"]
