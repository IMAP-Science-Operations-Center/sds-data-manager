#!/usr/bin/env python3
# Installed
from aws_cdk import App, Environment

# Local
from sds_data_manager.utils.stackbuilder import build_backup, build_sds

"""
This app is designed to be the dev and production deployment app.
It defaults to a dev deployment via a default `account_name` value in cdk.json.
To deploy to prod, specify `--context account_name=prod`.
To deploy to the backup account (only deploys required backup stacks),
specify `--context account_name=backup`.

"""

app = App()

# Grab values from context
# account_name is the section we are looking for parameters in
# within the cdk.json file:
#    "account_name": {"account": "0123", "region": "us-west-2", ...}
# This can be overridden via the command line: `--context account_name=prod`
account_name = app.node.get_context("account_name")

# once we have the account_name, get that section out of cdk.json
account_config = app.node.get_context(account_name)
# Add the account_name to the account_config for later lookup
# and using it within the stacks without needing to pass an extra variable
account_config["account_name"] = account_name
account = account_config["account"]
region = account_config["region"]

env = Environment(account=account, region=region)
print(f"Using the {account_name} account [{account}] in the {region} region.")

if account_name == "backup":
    # Deploy backup resources
    # The source account is the account that owns the source bucket.
    # Lookup the name of that account, then go and get that account
    # number from its own context section
    source_account_name = account_config["source_account"]
    source_account_config = app.node.get_context(source_account_name)
    source_account = source_account_config["account"]
    build_backup(app, env=env, source_account=source_account)
else:
    # Deploy SDS resources. This is the default with no CLI context variables set.
    build_sds(app, env=env, account_config=account_config)

app.synth()
