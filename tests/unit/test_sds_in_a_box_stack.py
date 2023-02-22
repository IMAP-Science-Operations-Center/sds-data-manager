###################################################
# NOTE: Doesn't actually do anything at the moment
###################################################

import aws_cdk as core
import aws_cdk.assertions as assertions
from sds_in_a_box.sds_in_a_box_stack import SdsInABoxStack

# example tests. To run these tests, uncomment this file along with the example
# resource in sds_in_a_box/sds_in_a_box_stack.py
def test_sqs_queue_created():
    stack = SdsInABoxStack(app, "sds-in-a-box", SDS_ID="unit-testing", initial_email = "123fake@email.com")
    template = assertions.Template.from_stack(stack)