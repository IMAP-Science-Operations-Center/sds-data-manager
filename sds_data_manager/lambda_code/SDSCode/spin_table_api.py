import json


def lambda_handler(event, context):
    print(event)
    # TODO: extend this lambda code once we finish creating
    # spin table schema
    return {"statusCode": 200, "body": json.dumps("Hello from Spin Table Lambda!")}
