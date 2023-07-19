#!/usr/bin/env python3
# Installed
from aws_cdk import App, Environment

# Local
from sds_data_manager.utils.stackbuilder import build_sds

"""
This app is designed to be the dev and production deployment app.
It defaults to a dev deployment via a default `env` value in cdk.json.
To deploy to prod, specify `--context env=prod`.
"""

app = App()

# Grab values from context
sds_region = app.node.try_get_context("sds-region")
where_to_deploy = app.node.try_get_context("env")
params = app.node.try_get_context(where_to_deploy)

# Ensure required parameters are present
if not params or "account" not in params or "sds_id" not in params:
    raise ValueError("Required context parameters 'account' and 'sds_id' not provided.")

account = params["account"]

env = Environment(account=account, region=sds_region)
print(f"Deploying to account {account} in region {sds_region}.")

# Deploy SDS resources. This is the default with no CLI context variables set.
stacks = build_sds(app, env=env, sds_id=params["sds_id"], use_custom_domain=True)

app.synth()
