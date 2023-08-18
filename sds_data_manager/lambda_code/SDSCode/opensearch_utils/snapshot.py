import logging
import string
from datetime import datetime

import boto3
import requests
from requests_aws4auth import AWS4Auth


def get_auth(region):
    """
    Gets AWS service and credentials for snapshot

    Parameters
    ----------
    region : str
        region of the deployed OpenSearch Instance

    Returns
    -------
    AWS4Auth
    """
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
    """Register the snapshot repository. A repository is
    an OpenSearch term for the storage location where the
    Snapshots will be stored. In this case, it is an S3 bucket.
    Parameters
    ----------
    payload : dict
             S3 bucket and AWS region to store the manual snapshots
             The role ARN that has S3 permissions to store the new snapshot
    url : str
        OpenSearch domain URL endpoint including https:// and trailing /.
    awsauth: AWS4Auth
        Credentials for use in snapshot requests
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
    awsauth: AWS4Auth
        Credentials for use in snapshot requests
    """

    r = requests.put(url, auth=awsauth)
    return r


def run_backup(host, region, snapshot_repo_name, snapshot_s3_bucket, snapshot_role_arn):
    """Creates a backup of the current state of OpenSearch. This includes registering
    a repository, if needed, as well as timestamping the snapshot name, constructing
    the snapshot url, and taking the snapshot.
        Parameters
    ----------
    host : str
        The OpenSearch domain endpoint (does not include https:// or trailing /)
    region : str
        The region where the OpenSearch instance is deployed
    snapshot_repo_name : str
        The name of the snpashot repository. This can be different than
        the S3 bucket name.
    snapshot_s3_bucket : str
        The name of the S3 bucket that will be used to store the Snapshots
    snapshot_role_arn : str
        The ARN of the Snapshot Role
    """
    awsauth = get_auth(region)
    snapshot_start_time: datetime = datetime.utcnow().strftime("%Y-%m-%d-%H:%M:%S")
    snapshot_name = f"opensearch_snapshot_{snapshot_start_time}"

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
            raise RuntimeError(f"{response.status_code}.{response.text}")
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
            raise RuntimeError(f"{response.status_code}.{response.text}")
    except RuntimeError as e:
        logging.info(
            f"Snapshot initiation for {snapshot_name} failed with error code/text: {e}"
        )
        raise
