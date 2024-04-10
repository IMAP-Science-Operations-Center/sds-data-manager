"""Module for testing API utilities."""

import argparse
import json
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _parse_args():
    """Parse the command line arguments.

    Returns
    -------
    args : argparse.Namespace
        An object containing the parsed arguments and their values

    """
    description = (
        "This command line program downloads"
        "a file from the s3_uri, modifies it, and uploads"
        "the modified file to the same s3 uri. "
        "Example usage: python cli.py <s3_uri>. "
    )

    api_endpoint_help = (
        "The api_endpoint. Default is https://api.dev.imap-mission.com. "
    )

    parser = argparse.ArgumentParser(prog="imap_api", description=description)

    parser.add_argument(
        "--instrument", type=str, required=True, help="Instrument name."
    )
    parser.add_argument(
        "--level", type=str, required=True, help="Data processing level."
    )
    parser.add_argument(
        "--s3_uri",
        type=str,
        required=True,
        help="Full path to the file in the S3 bucket.",
    )
    parser.add_argument(
        "--dependency",
        type=json.loads,
        required=True,
        help="Dependency information in JSON format.",
    )
    parser.add_argument("--api_endpoint", type=str, help=api_endpoint_help)
    args = parser.parse_args()

    return args


def download(s3_uri, api_endpoint="https://api.dev.imap-mission.com"):
    """Download a file from a given S3 URI via the specified API endpoint.

    Parameters
    ----------
    s3_uri : str
        The S3 URI of the file to be downloaded.
    api_endpoint : str, optional
        The API endpoint to use for downloading the file.

    Returns
    -------
    file_name_and_path : str
        The file path where the downloaded file is saved.

    """
    logger.info(f"Starting download from S3 URI: {s3_uri}")

    url_with_parameters = f"{api_endpoint}/download?{s3_uri}"
    response = requests.get(url_with_parameters, timeout=60)

    # Set the base directory
    base_directory = Path("/mnt/data")

    if not base_directory.exists():
        base_directory = Path.cwd()

    # Join the base directory with the file name
    file_name_and_path = base_directory / Path(s3_uri.replace("s3://", ""))

    # Make parent directories if they don't exist
    file_name_and_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_name_and_path, "wb") as file:
        file.write(response.content)

    logger.info(f"File downloaded and saved to: {file_name_and_path}")

    return str(file_name_and_path)


def upload(local_file_location, api_endpoint="https://api.dev.imap-mission.com"):
    """Upload a local file to a remote server using the specified API endpoint.

    Parameters
    ----------
    local_file_location : str
        The file path of the file to be uploaded.
    api_endpoint : str, optional
        The API endpoint to use for uploading the file.

    """
    logger.info(f"Starting upload for file: {local_file_location}")

    local_file_path = Path(local_file_location)
    remote_file_name = local_file_path.name

    # Modify descriptor in file name
    modified_file_name = remote_file_name.replace(
        remote_file_name[12:15], f"{remote_file_name[12:15]}-test"
    )
    # Upload the file
    url_with_parameters = f"{api_endpoint}/upload?filename={modified_file_name}"
    get_response = requests.get(url_with_parameters, timeout=60)
    upload_url = get_response.json()
    requests.put(upload_url, timeout=60)

    logger.info(f"File uploaded: {modified_file_name}")


def main():
    """Parse args and perform an upload via the API."""
    args = _parse_args()

    endpoint = (
        args.api_endpoint
        if args.api_endpoint is not None
        else "https://api.dev.imap-mission.com"
    )
    file_name_and_path = download(args.s3_uri, endpoint)
    upload(file_name_and_path, endpoint)

    logger.info("Process completed successfully")


if __name__ == "__main__":
    main()
