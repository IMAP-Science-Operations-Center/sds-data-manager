###################################################
# NOTE: Doesn't actually do anything at the moment
###################################################

import random
import string

import aws_cdk as core
from aws_cdk import assertions

from sds_data_manager.sds_data_manager_stack import SdsDataManagerStack


# This test just ensures the stack is able to be created
# Does not currently check the products that were created
def test_sds_data_manager_validity():
    app = core.App(context={"SDSID": "unit-testing"})
    sds_id = "".join([random.choice(string.ascii_lowercase) for i in range(8)])
    stack = SdsDataManagerStack(app, f"sds-data-manager-{sds_id}", sds_id)
    assertions.Template.from_stack(stack)
