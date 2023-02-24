import aws_cdk as core
import random
import string
import aws_cdk.assertions as assertions
from sds_in_a_box.sds_in_a_box_stack import SdsInABoxStack

# This test just ensures the stack is able to be created
# Does not currently check the products that were created
def test_sds_in_a_box_validity():
    app = core.App(context={"SDSID": "unit-testing"})
    SDS_ID = "".join( [random.choice(string.ascii_lowercase) for i in range(8)] )
    stack = SdsInABoxStack(app, f"sds-in-a-box-{SDS_ID}", SDS_ID, initial_email = "123fake@email.com")
    template = assertions.Template.from_stack(stack)