import random
import string

import aws_cdk as core
from aws_cdk import assertions

from sds_data_manager.sds_cognito_stack import SdsCognitoStack


# This test just ensures the stack is able to be created
# Does not currently check the products that were created
def test_sds_cognito_validity():
    app = core.App(context={"SDSID": "unit-testing"})
    sds_id = "".join([random.choice(string.ascii_lowercase) for i in range(8)])
    stack = SdsCognitoStack(app, f"sds-cognito-{sds_id}", sds_id)
    assertions.Template.from_stack(stack)
