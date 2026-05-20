# OS
FROM --platform=linux/amd64 mcr.microsoft.com/vscode/devcontainers/python:3.12
RUN sudo apt-get update
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs
RUN npm install -g aws-cdk
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
RUN unzip awscliv2.zip
RUN sudo ./aws/install
RUN sudo apt-get install -y python3 python3-pip

RUN pip install "poetry>=2.0"
ENV AWS_CONFIG_FILE=/workspaces/sds-data-manager/.aws/config
ENV AWS_SHARED_CREDENTIALS_FILE=/workspaces/sds-data-manager/.aws/credentials

WORKDIR /workspaces/sds-data-manager/
