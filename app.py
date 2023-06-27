#!/usr/bin/env python3
# Installed
from aws_cdk import App, Environment
# Local
from sds_data_manager.utils.stackbuilder import build_sdc

"""
This app is designed to be the dev and production deployment app. It defaults to a dev deployment 
via a default `deploy-where` value in cdk.json. To deploy to prod, specify `--context deploy-where=prod`.
"""

app = App()

# Grab values from context
sdc_region = app.node.try_get_context('sdc-region')
where_to_deploy = app.node.try_get_context('deploy-where')
params = app.node.try_get_context(where_to_deploy)

# Ensure required parameters are present
if not params or 'account' not in params or 'sds_id' not in params:
    raise ValueError("Required context parameters 'account' and 'sds_id' not provided.")

account = params["account"]

env = Environment(account=account, region=sdc_region)
print(f"Deploying to account {account} in region {sdc_region}.")

# Deploy Libera SDC resources. This is the default with no CLI context variables set.
stacks = build_sdc(app,
                   env=env,
                   sds_id=params["sds_id"])

app.synth()

