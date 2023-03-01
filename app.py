#!/usr/bin/env python3
import os
import boto3 
import aws_cdk as cdk
import aws_cdk.assertions as assertions
from sds_in_a_box.sds_in_a_box_stack import SdsInABoxStack


app = cdk.App()
SdsInABoxStack(app, "SdsInABoxStack")

app.synth()

#app = cdk.App()
#stack = SdsInABoxStack(app, "sds-in-a-box")
#template = assertions.Template.from_stack(stack)
#print(template)