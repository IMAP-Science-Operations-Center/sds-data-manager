"""IALiRT alarm lambda."""

import os

import boto3


def lambda_handler(event, context):
    """Reset I-ALiRT alarm to an ok state.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    Returns
    -------
    response : dict
        The response from the cloudwatch client.
    """
    client = boto3.client("cloudwatch")
    alarm_name = os.environ["ALARM_NAME"]

    response = client.set_alarm_state(
        AlarmName=alarm_name,
        StateValue="OK",
        StateReason="Resetting alarm daily to re-trigger if no packets arrive.",
    )

    return {"status": "alarm reset", "response": response}
