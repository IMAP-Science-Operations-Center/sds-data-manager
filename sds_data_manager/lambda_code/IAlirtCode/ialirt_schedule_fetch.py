"""Lambda to poll an external HTTPS endpoint for a contact schedule XML file."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import boto3
import requests

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_secret(secret_name: str, region: str) -> str:
    """Retrieve a secret value from AWS Secrets Manager.

    Parameters
    ----------
    secret_name : str
        The name or ARN of the secret.
    region : str
        The AWS region.

    Returns
    -------
    str
        The secret string value.
    """
    client = boto3.client("secretsmanager", region_name=region)
    return client.get_secret_value(SecretId=secret_name)["SecretString"]


def write_temp_file(content: str, filename: str) -> Path:
    """Write content to a temporary file in /tmp.

    Parameters
    ----------
    content : str
        The file content to write.
    filename : str
        The filename to write to under /tmp.

    Returns
    -------
    Path
        Path to the written file.
    """
    path = Path("/tmp") / filename  # noqa: S108
    path.write_text(content)
    return path


def fetch_schedule_xml(url: str, cert_path: Path, key_path: Path) -> str:
    """Fetch the contact schedule XML from the external HTTPS endpoint.

    Parameters
    ----------
    url : str
        The HTTPS endpoint URL.
    cert_path : Path
        Path to the SSL client certificate file.
    key_path : Path
        Path to the SSL client key file.

    Returns
    -------
    str
        The raw XML response body.
    """
    logger.info(f"Fetching schedule from {url}")
    response = requests.get(url, cert=(str(cert_path), str(key_path)), timeout=30)
    response.raise_for_status()
    logger.info(f"Received response: {response.status_code}")
    return response.text


def lambda_handler(event, context):
    """Poll external HTTPS endpoint for contact schedule XML."""
    logger.info("Starting schedule fetch.")

    url = os.environ.get("SCHEDULE_ENDPOINT_URL")
    cert_secret_name = os.environ.get("CERT_SECRET_NAME")
    key_secret_name = os.environ.get("KEY_SECRET_NAME")
    region = os.environ.get("AWS_REGION")

    if not url or not cert_secret_name or not key_secret_name:
        logger.info(
            "SCHEDULE_ENDPOINT_URL, CERT_SECRET_NAME, "
            "and KEY_SECRET_NAME are required. "
            "Skipping schedule fetch."
        )
        return

    cert_path = write_temp_file(get_secret(cert_secret_name, region), "client.crt")
    key_path = write_temp_file(get_secret(key_secret_name, region), "client.key")

    xml_content = fetch_schedule_xml(url, cert_path, key_path)

    bucket = os.environ.get("S3_BUCKET")
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    s3_key = f"ground_station_schedules/uksa/imap_ialirt_uksa-schedule_{date_str}.xml"
    boto3.client("s3").put_object(Bucket=bucket, Key=s3_key, Body=xml_content)
    logger.info(f"Saved schedule to s3://{bucket}/{s3_key}")
