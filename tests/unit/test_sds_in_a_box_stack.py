###################################################
# NOTE: Doesn't actually do anything at the moment
###################################################

import aws_cdk as core
import aws_cdk.assertions as assertions
from sds_in_a_box.sds_in_a_box_stack import SdsInABoxStack

# example tests. To run these tests, uncomment this file along with the example
# resource in sds_in_a_box/sds_in_a_box_stack.py
def test_sqs_queue_created():
    app = core.App(context={"SDSID": "unit-testing", "initial_user": "123fake@email.com"})
    stack = SdsInABoxStack(app, "sds-in-a-box")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
