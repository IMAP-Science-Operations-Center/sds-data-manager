import logging
import string
from datetime import datetime

import boto3
import requests
from requests_aws4auth import AWS4Auth


def get_auth(region):
    # Get AWS service and credentials for snapshot
    service = "es"
    credentials = boto3.Session().get_credentials()
    awsauth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        region,
        service,
        session_token=credentials.token,
    )

    return awsauth


def register_repo(payload: dict, url: string, awsauth):
    """Register the snapshot repo
    Parameters
    ----------
    payload : dict
             S3 bucket and AWS region to store the manual snapshots
             The role ARN that has S3 permissions to store the new snapshot
    url : str
        OpenSearch domain URL endpoint including https:// and trailing /.
    """

    headers = {"Content-Type": "application/json"}

    r = requests.put(url, auth=awsauth, json=payload, headers=headers)

    return r


def take_snapshot(url: string, awsauth):
    """Initiate a new snapshot
        Parameters
    ----------
    url : str
        OpenSearch domain URL endpoint including https:// and trailing /.
    """

    r = requests.put(url, auth=awsauth)
    return r


def run_backup(host, region, snapshot_repo_name, snapshot_s3_bucket, snapshot_role_arn):
    awsauth = get_auth(region)
    snapshot_start_time: datetime = datetime.utcnow().strftime("%Y-%m-%d-%H:%M:%S")
    snapshot_name = f"os_snapshot_{snapshot_start_time}"

    logging.info(f"Starting process for snapshot: {snapshot_name}.")

    # Register the snapshot, this can be run every time, if the
    # repo is registered will return 200
    try:
        path = f"_snapshot/{snapshot_repo_name}"  # the OpenSearch API endpoint
        url = "https://" + host + "/" + path

        payload = {
            "type": "s3",
            "settings": {
                "bucket": f"{snapshot_s3_bucket}",
                "region": f"{region}",
                "role_arn": f"{snapshot_role_arn}",
            },
        }
        response = register_repo(payload, url, awsauth)
        if response.status_code == 200:
            logging.info("Repo successfully registered")
        else:
            raise Exception(f"{response.status_code}.{response.text}")
    except Exception as e:
        logging.info(
            f"Snapshot repo registration: \
            {snapshot_repo_name} failed with error code/text: {e}"
        )
        raise

    # Initiate a new manual snapshot
    try:
        path = f"_snapshot/{snapshot_repo_name}/{snapshot_name}"
        url = "https://" + host + "/" + path
        response = take_snapshot(url, awsauth)
        if response.status_code == 200:
            logging.info(f"Snapshot {snapshot_name} initiated.")
        else:
            raise Exception(f"{response.status_code}.{response.text}")
    except Exception as e:
        logging.info(
            f"Snapshot initiation for {snapshot_name} failed with error code/text: {e}"
        )
        raise
