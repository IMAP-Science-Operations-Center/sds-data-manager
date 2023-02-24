#!/usr/bin/env python3
import os
import boto3 
import string
import random
import aws_cdk as cdk
import aws_cdk.assertions as assertions
from sds_in_a_box.sds_in_a_box_stack import SdsInABoxStack

app = cdk.App()

SDS_ID = app.node.try_get_context("SDSID")
initial_user=app.node.try_get_context("initial_user")

if SDS_ID is None:
    raise ValueError("ERROR: Need to specify an ID to name the stack (ex - production, testing, etc)")
elif SDS_ID=="random":
    # A random unqiue ID for this particular instance of the SDS
    SDS_ID = "".join( [random.choice(string.ascii_lowercase) for i in range(8)] )
    

SdsInABoxStack(app, f"SdsInABoxStack-{SDS_ID}", SDS_ID=SDS_ID, initial_email=initial_user)
app.synth()