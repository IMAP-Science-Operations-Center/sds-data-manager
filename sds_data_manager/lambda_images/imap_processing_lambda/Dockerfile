# If AWS Lambda uses image, it needs to use image from
# 1. https://gallery.ecr.aws/lambda?page=1
#    This gives us some option to install extra library like git
# 2. https://hub.docker.com/r/amazon/aws-lambda-python/
#    This doesn't give option to install extra library

FROM public.ecr.aws/lambda/python:3.11

# Install git
RUN yum install -y git

# Install pip and install imap-processing module
RUN python -m pip install --upgrade pip
RUN pip install git+https://github.com/IMAP-Science-Operations-Center/imap_processing.git@dev

# Lambda's default working directory in /var/task
# We can retrive using this environment variable
# ${LAMBDA_TASK_ROOT}
WORKDIR ${LAMBDA_TASK_ROOT}
# Install imap-processing using below command
COPY script.py .

CMD ["script.handler" ]
