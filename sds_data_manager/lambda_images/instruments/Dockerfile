# TODO I think we should have a single Dockerfile and pyproject.toml
# for all dockerized lambdas
FROM public.ecr.aws/lambda/python:3.11

# Install git
RUN yum install -y git

# Install development tools and required libraries
RUN pip install psycopg2-binary

# Install pip and install imap-processing module
RUN python -m pip install --upgrade pip

WORKDIR ${LAMBDA_TASK_ROOT}
COPY batch_starter.py .

CMD ["batch_starter.lambda_handler"]
