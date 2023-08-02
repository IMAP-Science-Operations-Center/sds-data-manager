#!/usr/bin/env python3
"""Template for CDK Application"""
# Standard
import os

# Installed
from aws_cdk import App, Environment

# Local
from sds_data_manager.utils.stackbuilder import build_sds

"""
    This app is used for individual developer testing, instead of app.py.
    When deploying locally, you can update the sds_id to include your
    initials and distinguish your stack from the main dev stack or
    other developers.

    IMPORTANT: You will need to make a copy of app_template_dev.py file
    with a different name (app_<name>_dev.py) and keep a copy of it
    locally so that it will not be committed.

    To deploy this app:

1. Install the required tools and activate the virtual environment:
    - nvm use
    - npm install -g aws-cdk
    - source .env/bin/activate

2. Set the appropriate environment variables:
    - export AWS_PROFILE=<profile>

3. Run the CDK commands:
    - cdk synth --app "python app_template_dev.py"
    - cdk diff --app "python app_template_dev.py"
    - cdk deploy --app "python app_template_dev.py" [ stack | --all ]
    - cdk destroy --app "python app_template_dev.py" [ stack | --all ]

"""
# Update with the AWS profile name you want to require for these builds
REQUIRED_PROFILE = "<profile>"
# Update with your initials or some other identifier
INITIALS = "<initials>"

current_profile = os.environ.get("AWS_PROFILE", "")
if current_profile != REQUIRED_PROFILE:
    raise ValueError(
        f"Wrong AWS Account set! Got: [{current_profile}], "
        f"but expected: [{REQUIRED_PROFILE}]"
    )

# These are set based on the current AWS profile
region = os.environ["CDK_DEFAULT_REGION"]
account = os.environ["CDK_DEFAULT_ACCOUNT"]

env = Environment(account=account, region=region)
app = App()
params = app.node.try_get_context("dev")
# sds_id = "abc-dev"
sds_id = f"{INITIALS}-{params['sds_id']}"

print(f"Using the profile [{current_profile}] in region [{region}].")
print(f"The stack identifier being used is: {sds_id}")

stacks = build_sds(
    app,
    env=env,
    sds_id=sds_id,
    use_custom_domain=True,
)

app.synth()
