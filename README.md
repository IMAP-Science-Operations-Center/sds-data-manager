# SDS-data-manager

This project is the core of a Science Data System.  

Our goal with the project is that users will only need to modify the file config.json to define the data products stored on the SDC, and the rest should be mission agnostic.  

## Architecture

The code in this repository takes the form of an AWS CDK project. It provides the architecture for:

1. An HTTPS API to upload files to an S3 bucket (*in development*)
2. An S3 bucket to contain uploaded files
3. An HTTPS API to query and download files from the S3 bucket (*in development*)
4. A lambda function that inserts file metadata into an opensearch instance
5. A Cognito User Pool that keeps track of who can access the restricted APIs.  

## Development (Codespaces)

The development environment optionally uses a GitHub codespace, to ensure that we're all using the proper libraries as we develop and deploy.  The development environment configuration is kept in the .devcontainer folder.  

To start a new development environment, click the button for "Code" in the upper right corner of the repository, and click "Codespaces".  

## Development (Local)

If you are running locally, you will need to install [cdk](https://docs.aws.amazon.com/cdk/v2/guide/getting_started.html) and [poetry](https://python-poetry.org/docs/#installation). 

### Poetry set up
If you're running locally, you can install the Python requirements with Poetry:

```
poetry install
```

This will install the dependencies from `poetry.lock`, ensuring that consistent versions are used. Poetry also provides a virtual environment, which you will have to activate.

```
poetry shell
```

If running in codespaces, this should already be done.


## AWS Setup

The first thing you'll need to do is configure your aws environment with:

```bash
aws configure
```

Enter in your AWS Access Key ID and AWS Secret Access Key, which can be obtained by setting up a user account in the AWS console. For region, set it to the AWS region you'd like to set up your SDS. For IMAP, we're using "us-west-2"

If you have multiple AWS access/secret key pairs locally, you can add the configuration to `~/.aws/config`. 

```
[imap]
region=us-west-2
aws_access_key_id=<Access Key>
aws_secret_access_key=<Secret Key>
```

Then, you can set the profile used by cdk by setting the `AWS_PROFILE` environment variable to the profile name (in this case, imap):

```
export AWS_PROFILE=imap
```

You may also need to set the `CDK_DEFAULT_ACCOUNT` environment variable. 

**NOTE**-- If this is a brand new AWS account, then you'll need to bootstrap your account to allow CDK deployment with the command: 

```bash
cdk bootstrap
```

If you get errors with this command, running with `-v` will provide more information. 

## Deploy

There are several important configuration items which you can alter during a deployment:

- SDSID - An ID number appended to the name of all of your services.  For example, "prod", "dev", or "harter-testing-stack"
- initial_user - Optional. If this email address is provided, Cognito will send an email with instructions on setting up a Cognito 
- userpool_name - Optional (Required if app_client_name given). This is the name of the userpool that contains the specified app_client_name.
- app_client_name - Optional (Required if userpool_name given). This is the name of the app client on the AWS account that will be used to verify received tokens in the API.  
- cognito_only - Optional.  If this context is set, then ONLY cognito services will be created. 

To deploy the SDS, first you should synthesize the CDK code with the command to check for errors:

```bash
cdk synth --context SDSID={insert a unique ID here}
```

When you are ready, you can deploy the architecture with the following command:

```bash
cdk deploy --all --context SDSID={insert a unique ID here}
```

for example:

```bash
cdk synth --context SDSID=username-testing
cdk deploy --all --context SDSID=username-testing
```

After about 20 minutes or so, you should have a brand new SDS set up in AWS.  



## Virtual Desktop for Development

Codespaces comes with a fully functional virtual desktop.  To open, click on the "ports" tab and then "open in new browser". The default password is "vscode".

## Testing the APIs

Inside of the "scripts" folder is a python script you can use to call the APIs.  It is completely independent of the rest of the project, so you should be able to pull this single file out and run it anywhere.  It only depends on basic python libraries.

Unfortunately right now you need to "hard code" in the lambda API URL and the Cognito App Client at the top of the file after every build.  I'm hoping in the future to determine a better way to automate this.
