# SDS-data-manager

This project is the core of a Science Data System.

Our goal with the project is that users will only need to modify the file config.json to define the data products stored on the SDS, and the rest should be mission agnostic.

## Requirements
- AWS CLI [download link](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- nodejs [download link](https://nodejs.org/en/download/)
- Docker [download link](https://docs.docker.com/get-docker/)

## Architecture

The code in this repository takes the form of an AWS CDK project. It provides the architecture for:

1. An HTTPS API to upload files to an S3 bucket (*in development*)
2. An S3 bucket to contain uploaded files
3. An HTTPS API to query and download files from the S3 bucket (*in development*)
4. A lambda function that inserts file metadata into an opensearch instance
5. A Cognito User Pool that keeps track of who can access the restricted APIs.

## Development

The development environment uses a GitHub codespace, to ensure that we're all using the proper libraries as we develop and deploy.

Everyone gets 50 free hours per month of github Codespace time.  Alternatively, your organization can pay for it to run longer than this.

To start a new development environment, click the button for "Code" in the upper right corner of the repository, and click "Codespaces".

If you are running locally, you will need to install [cdk](https://docs.aws.amazon.com/cdk/v2/guide/getting_started.html) and [poetry](https://python-poetry.org/docs/#installation).

### Poetry set up
If you're running locally, you can install the Python requirements with Poetry:

```
poetry install
```

To install all extras

```
poetry install --all-extras
```

This will install the dependencies from `poetry.lock`, ensuring that consistent versions are used. Poetry also provides a virtual environment, which you will have to activate.

```
poetry shell
```

If running in codespaces, this should already be done.


### AWS Setup

[AWS Setup page](https://sds-data-manager.readthedocs.io/en/latest/cdk/aws-setup.html)

You may also need to set the `CDK_DEFAULT_ACCOUNT` environment variable.

**NOTE**-- For new AWS users, you'll need to make certain the AWS Cloud Development Kit is installed:


```bash
nvm use <version>
npm install -g aws-cdk
```

**NOTE**-- If this is a brand-new AWS account (IMPORTANT: new account, not new user), then you'll need to bootstrap your account to allow CDK deployment with the command:

```bash
cdk bootstrap
```

If you get errors with the 'cdk bootstrap' command, running with `-v` will provide more information.

### Deploy

[CDK Deployment page](https://sds-data-manager.readthedocs.io/en/latest/cdk/cdk-deployment.html)

### Virtual Desktop for Development

Codespaces actually comes with a fully functional virtual desktop.  To open, click on the "ports" tab and then "open in new browser". The default password is "vscode".

### Testing the APIs

Inside of the "scripts" folder is a python script you can use to call the APIs.  It is completely independent of the rest of the project, so you should be able to pull this single file out and run it anywhere.  It only depends on basic python libraries.

Unfortunately right now you need to "hard code" in the lambda API URL and the Cognito App Client at the top of the file after every build.  I'm hoping in the future to determine a better way to automate this.
