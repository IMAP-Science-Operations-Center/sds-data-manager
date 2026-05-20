FROM python:3.10-slim

# System environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_VERSION=2.4.1 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_CREATE=false \
    DAGSTER_HOME="/opt/dagster/dagster_home" \
    PYTHONPATH="/opt/dagster/app"

# Add Poetry to PATH
ENV PATH="$POETRY_HOME/bin:$PATH"

# Install system dependencies needed for Poetry and PostgreSQL
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    libpq-dev \
    && curl -sSL https://install.python-poetry.org | python3 - \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /opt/dagster/app

# Copy dependency management files first to leverage Docker layer caching
COPY pyproject.toml README.md poetry.lock* ./

# Copy internal project directories into the container
COPY sds_data_manager/ ./sds_data_manager/
COPY orchestration/ ./orchestration/

RUN poetry install --with layer-database && poetry install --with layer-spice && poetry install --with layer-processing && poetry install --with cdk-install

# Set up the Dagster home directory and copy the system configuration
# Dagster looks for dagster.yaml in $DAGSTER_HOME
RUN mkdir -p $DAGSTER_HOME 
# && cp orchestration/dagster.yaml $DAGSTER_HOME/

# The CMD is technically overridden by the CDK for the Webserver and Daemon tasks,
# but providing a default makes the image easier to test locally.
CMD ["dagster-webserver", "--read-only", "-h", "0.0.0.0", "-p", "3000", "-w", "orchestration/workspace.yaml"]