import logging
import os
from pathlib import Path

import boto3

# Define the paths
mount_path = Path(os.environ.get("EFS_MOUNT_PATH"))

attitude_symlink_path = mount_path / "latest_attitude_kernel.ah.a"
ephemeris_symlink_path = mount_path / "latest_ephemeris_kernel.bsp"


def create_symlink(source_path: Path, destination_path: Path) -> None:
    """Create a symlink from source_path to destination_path

    Parameters
    ----------
    source_path : str
        Source path of the symlink
    destination_path : str
        Destination path of the symlink
    """

    # Remove the old symlink
    destination_path.unlink(missing_ok=True)

    # Create a new symlink pointing to the new file
    destination_path.symlink_to(source_path)


def write_data_to_efs(s3_key: str, s3_bucket: str):
    """Write data to EFS and create/update symlink

    Parameters
    ----------
    s3_key : str
        S3 object key
    s3_bucket : str
    """
    filename = os.path.basename(s3_key)

    # Create an S3 client
    s3_client = boto3.client("s3")

    # Download the file to the mount directory. Eg. /mnt/efs
    download_path = mount_path / filename

    try:
        # Download the file from S3
        s3_client.download_file(s3_bucket, s3_key, download_path)
        logging.debug(f"File downloaded: {download_path}")
    except Exception as e:
        logging.info(f"Error downloading file: {e!s}")

    logging.debug("After downloading file: %s", os.listdir(mount_path))

    # TODO: we only want historical attitude kernels delivery
    #  following each track (3/wk)
    # Make certain it does not start with imap_pred_ (only imap_)
    # https://confluence.lasp.colorado.edu/display/IMAP/IMAP+POC+External+Reference+Documents
    # pg 17 of 7516-9163 MOC Data Products Guide
    # Historical attitude naming convention is this:
    # imap_yyyy_doy_yyyy_doy_##.ah.bc and
    # imap_yyyy_doy_yyyy_doy_##.ah.a
    if (
        filename.endswith(".ah.a")
        or filename.endswith(".ah.bc")
        and not filename.startswith("imap_pred_")
    ):
        create_symlink(download_path, attitude_symlink_path)

    # TODO: reconstructed would be ideal (change to imap_recon)
    # Reconstructed delivered 1/wk
    # But nom is least ideal (after burn and pred)
    # Ephemeris naming convention is this:
    # Eg. imap_nom_yyyymmdd_yyyymmdd_v##.bsp
    # The reason we check startswith is because other ephemeris
    # kernel has different prefix. Eg.
    #   imap_recon_yyyymmdd_yyyymmdd_v##.bsp
    #   imap_burn_yyyymmdd_yyyymmdd_v##.bsp
    #   imap_pred_yyyymmdd_yyyymmdd_v##.bsp
    elif filename.startswith("imap_recon") and filename.endswith(".bsp"):
        create_symlink(download_path, ephemeris_symlink_path)
    elif filename.startswith("imap_burn") and filename.endswith(".bsp"):
        create_symlink(download_path, ephemeris_symlink_path)
    elif filename.startswith("imap_pred") and filename.endswith(".bsp"):
        create_symlink(download_path, ephemeris_symlink_path)


def lambda_handler(event, context):
    """This lambda  is trigger by eventbridge.

    Input looks like this:
    {
        "version": "0",
        "id": "3ee8fb2e-856d-790d-1d81-f77e1f3c0987",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "account": "449431850278",
        "time": "2023-10-25T23:53:17Z",
        "region": "us-west-2",
        "resources": [
            "arn:aws:s3:::sds-data-449431850278"
        ],
        "detail": {
            "version": "0",
            "bucket": {
                "name": "sds-data-449431850278"
            },
            "object": {
                "key": "imap_nom_20231024_20231025_v00.bsp",
                "size": 8,
                "etag": "fd33e2e8ad3cb1bdd3ea8f5633fcf5c7",
                "version-id": "w9eElv_lFFeEbifMabOBHjtJl9Ori_At",
                "sequencer": "006539AA6D7936ACF5"
            },
            "request-id": "5V837ESMXGRD39D2",
            "requester": "449431850278",
            "source-ip-address": "128.138.64.30",
            "reason": "PutObject"
        }
    }

    Parameters
    ----------
    event : dict
        Event input
    context : LambdaContext
        This object provides methods and properties that provide information
        about the invocation, function, and runtime environment.

    Returns
    -------
    dict
        Response message
    """
    # Retrieve the S3 bucket and key from the event
    s3_bucket = event["detail"]["bucket"]["name"]
    s3_key = event["detail"]["object"]["key"]
    logging.debug(event)

    write_data_to_efs(s3_key, s3_bucket)

    return {"statusCode": 200, "body": "File downloaded and moved successfully"}
