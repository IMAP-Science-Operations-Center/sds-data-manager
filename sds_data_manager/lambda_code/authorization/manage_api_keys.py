#!/usr/bin/env python3
"""Simple script to manage API keys in AWS SSM Parameter Store (SecureString).

AWS_PROFILE and AWS_REGION environment variables should be set to the appropriate values
for the account and region where the SSM parameter is stored.

Usage:
  python manage_api_keys.py list
  python manage_api_keys.py add <owner> <email> <scope>
  python manage_api_keys.py remove <key>
  AWS_PROFILE=imap-sdc-dev AWS_DEFAULT_REGION=us-west-2 \
    python sds_data_manager/lambda_code/authorization/manage_api_keys.py \
        add "First Last" "user@example.com" "full"

Requires AWS credentials with SSM permissions.
"""

import json
import secrets
import sys
from datetime import datetime

import boto3

PARAM_NAME = "imap-sdc-api-keys"
ssm = boto3.client("ssm")


def get_keys():
    """Retrieve API keys and metadata from SSM Parameter Store as a dict."""
    try:
        resp = ssm.get_parameter(Name=PARAM_NAME, WithDecryption=True)
        return json.loads(resp["Parameter"]["Value"])
    except ssm.exceptions.ParameterNotFound:
        return {}
    except Exception as e:
        print(f"Error retrieving parameter: {e}")
        sys.exit(1)


def put_keys(keys):
    """Store API keys and metadata in SSM Parameter Store as JSON."""
    value = json.dumps(keys, indent=2)
    try:
        ssm.put_parameter(
            Name=PARAM_NAME,
            Value=value,
            Type="SecureString",
            Overwrite=True,
            Tier="Advanced",
        )
        print(f"Updated {PARAM_NAME} with {len(keys)} key(s).")
    except Exception as e:
        print(f"Error updating parameter: {e}")
        sys.exit(1)


def list_keys():
    """List current API keys and their metadata."""
    keys = get_keys()
    print("Current API Keys:")
    for k, meta in keys.items():
        owner = meta.get("owner", "?")
        email = meta.get("email", "?")
        scope = meta.get("scope", "?")
        created = meta.get("created", "?")
        print(
            f"- {k}\n    owner={owner}, email={email}, scope={scope}, created={created}"
        )


def add_key(owner, email, scope="full"):
    """Generate and add a new API key with owner and email metadata."""
    keys = get_keys()
    # Generate a secure random 32-byte hex key
    new_key = secrets.token_hex(32)
    while new_key in keys:
        new_key = secrets.token_hex(32)
    keys[new_key] = {
        "owner": owner,
        "email": email,
        "created": datetime.now().isoformat(),
        "scope": scope,
    }
    put_keys(keys)
    print(f"Added key: {new_key}")
    print("Share this key securely with the user.")


def remove_key(key):
    """Remove an API key."""
    keys = get_keys()
    if key not in keys:
        print("Key not found.")
        return
    del keys[key]
    put_keys(keys)
    print(f"Removed key: {key}")


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print(
            "Usage: python manage_api_keys.py [list|add|remove] "
            "<key> [owner] [email] [scope]"
        )
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "list":
        list_keys()
    elif cmd == "add" and len(sys.argv) == 5:
        add_key(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "remove" and len(sys.argv) == 3:
        remove_key(sys.argv[2])
    else:
        print(
            "Usage: python manage_api_keys.py [list|add|remove] "
            "<key> [owner] [email] [scope]"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
