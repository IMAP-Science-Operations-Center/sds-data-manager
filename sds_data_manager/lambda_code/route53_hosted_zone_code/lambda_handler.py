"""Domain name authorization lambda to verify subdomains."""

import json
import os

import boto3


def lambda_handler(event, context):  # noqa
    try:
        # Expected JSON payload:
        expected_payload = (
            '{"ns_values": ["<NS record 1>", "<NS record 2>", '
            '"<NS record 3>", "<NS record 4>"], '
            '"subdomain": "<subdomain>"}'
        )

        # Check for unauthorized IPs
        ip = event.get("headers", "{}").get("x-forwarded-for", "")
        if ip != os.environ["allowed_ip"]:
            print(f"Unauthorized IP: {ip}")
            return {"statusCode": 403, "body": "Error: Unauthorized IP"}

        # Check if JSON payload contains a body
        if "body" not in event:
            return {
                "statusCode": 400,
                "body": (
                    f"Bad Request: Missing body in JSON payload."
                    f"\nExpected payload: {expected_payload}"
                ),
            }
        body = json.loads(event.get("body", "{}"))

        # Extract the NS values from the JSON payload
        ns_values = body.get("ns_values", [])
        # Error check
        if not isinstance(ns_values, list) or len(ns_values) != 4:
            return {
                "statusCode": 400,
                "body": (
                    f"Bad Request: JSON payload must contain 4 NS records."
                    f"\nExpected payload: {expected_payload}"
                ),
            }

        # Extract subdomain name from the JSON payload
        subdomain = body.get("subdomain", "")
        # Error check
        if not isinstance(subdomain, str) or not subdomain:
            return {
                "statusCode": 400,
                "body": (
                    f"Bad Request: subdomain must be a non-empty string."
                    f"\nExpected payload: {expected_payload}"
                ),
            }
        # adding the apex domain to the given subdomain
        subdomain += f".{os.environ["apex_domain_name"]}"

        # Getting the hosted zone ID
        hosted_zone_id = os.environ["hosted_zone_id"]

        # Set up Route 53 client
        route53 = boto3.client("route53")

        # Checking that an A record with the same name as the subdomain does not exist
        # in the hosted zone
        res = route53.list_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            StartRecordName=subdomain,
            StartRecordType="A",
        )

        for record in res["ResourceRecordSets"]:
            if record["Type"] == "A" and record["Name"] == f"{subdomain}.":
                return {
                    "statusCode": 400,
                    "body": (
                        f"Error: An A record with the name {subdomain} "
                        f"already exists"
                    ),
                }

        # Create or update the subdomain
        route53.change_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            ChangeBatch={
                "Changes": [
                    {
                        "Action": "UPSERT",
                        "ResourceRecordSet": {
                            "Name": subdomain,
                            "Type": "NS",
                            "TTL": 300,
                            "ResourceRecords": [{"Value": ns} for ns in ns_values],
                        },
                    }
                ]
            },
        )

        return {"statusCode": 200, "body": "NS record updated successfully!"}
    except json.JSONDecodeError:
        return {
            "statusCode": 400,
            "body": (
                f"Bad Request: Invalid JSON payload.\n"
                f"Expected payload: {expected_payload}"
            ),
        }
    except Exception as e:
        return {"statusCode": 500, "body": f"Error: {e!s}"}
