#!/usr/bin/env python3
"""Simple script to manage API keys in AWS DynamoDB.

AWS_PROFILE and AWS_REGION environment variables should be set to the appropriate values
for the account and region where the DynamoDB table is located.

Usage:
  python manage_api_keys.py list
  python manage_api_keys.py add <owner> <email> <scope>
  python manage_api_keys.py remove <key>
  AWS_PROFILE=imap-sdc-dev AWS_DEFAULT_REGION=us-west-2 \
    python sds_data_manager/lambda_code/authorization/manage_api_keys.py \
        add "First Last" "user@example.com" "full"

Requires AWS credentials with DynamoDB permissions.
"""

import secrets
import sys
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

TABLE_NAME = "imap-sdc-api-keys"

# Validate scope
VALID_SCOPES = {
    "full",
    "read",
}


def get_table():
    """Get the DynamoDB table resource."""
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(TABLE_NAME)


def get_keys():
    """Retrieve API keys and metadata from DynamoDB as a dict."""
    try:
        table = get_table()
        response = table.scan()
        keys = {}
        for item in response["Items"]:
            api_key = item["api_key"]
            # Convert DynamoDB item to the expected format
            keys[api_key] = {
                "owner": item.get("owner", ""),
                "email": item.get("email", ""),
                "scope": item.get("scope", "full"),
                "created": item.get("created", ""),
            }
        return keys
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(
                f"DynamoDB table '{TABLE_NAME}' not found. "
                "Please create the table first."
            )
            print("Table should have 'api_key' as the primary key (string).")
            sys.exit(1)
        else:
            print(f"Error retrieving keys from DynamoDB: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"Error retrieving keys: {e}")
        sys.exit(1)


def add_key_to_db(api_key, owner, email, scope, created):
    """Add a single API key to DynamoDB."""
    try:
        table = get_table()
        table.put_item(
            Item={
                "api_key": api_key,
                "owner": owner,
                "email": email,
                "scope": scope,
                "created": created,
            }
        )
    except Exception as e:
        print(f"Error adding key to DynamoDB: {e}")
        sys.exit(1)


def remove_key_from_db(api_key):
    """Remove a single API key from DynamoDB."""
    try:
        table = get_table()
        table.delete_item(Key={"api_key": api_key})
    except Exception as e:
        print(f"Error removing key from DynamoDB: {e}")
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
    """Generate and add a new API key with owner and email metadata.

    Parameters
    ----------
    owner : str
        The owner name of the API key
    email : str
        The email associated with the API key
    scope : str
        The scope/permission level for the key. Valid values are:
        - 'full': Full read and write access
        - 'read': Read-only access
        Default is 'full'
    """
    if scope not in VALID_SCOPES:
        valid_scopes_str = ", ".join(sorted(VALID_SCOPES))
        print(f"Error: Invalid scope '{scope}'. Valid scopes are: {valid_scopes_str}")
        return

    keys = get_keys()
    # Generate a secure random 32-byte hex key
    new_key = secrets.token_hex(32)
    while new_key in keys:
        new_key = secrets.token_hex(32)

    created = datetime.now().isoformat()
    add_key_to_db(new_key, owner, email, scope, created)
    print(f"Added key: {new_key}")
    print(f"Scope: {scope}")
    print("Share this key securely with the user.")


def remove_key(key):
    """Remove an API key."""
    keys = get_keys()
    if key not in keys:
        print("Key not found.")
        return
    remove_key_from_db(key)
    print(f"Removed key: {key}")


def update_permission(owner: str, email: str, scope: str):
    """Update permissions for API key.

    Parameters
    ----------
    owner : str
        The owner name of the API key
    email : str
        The email associated with the API key
    scope : str
        The new scope/permission level. Valid values are:
        - 'full': Full read and write access
        - 'read': Read-only access
    """
    if scope not in VALID_SCOPES:
        valid_scopes_str = ", ".join(sorted(VALID_SCOPES))
        print(f"Error: Invalid scope '{scope}'. Valid scopes are: {valid_scopes_str}")
        return

    table = get_table()
    keys = get_keys()

    matches = [
        key
        for key, value in keys.items()
        if value["owner"] == owner and value["email"] == email
    ]

    if matches:
        key = keys[matches[0]]
        table.put_item(
            Item={
                "api_key": matches[0],
                "owner": key["owner"],
                "email": key["email"],
                "scope": scope,
                "created": key["created"],
            }
        )
        print(f"Updated key permission for: {owner}, {email}")
        print(f"New scope: {scope}")
    else:
        print(
            f"Update not performed since no api key match found for: {owner}, {email}."
        )


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
    elif cmd == "update_permission":
        update_permission(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        print(
            "Usage: python manage_api_keys.py [list|add|remove] "
            "<key> [owner] [email] [scope]"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
