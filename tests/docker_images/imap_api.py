"""Module for testing API utilities.
"""
import argparse
import logging
import os

import requests

logging.basicConfig(level=logging.INFO)


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
        "Example usage: python imap_api.py <s3_uri>. "
    )

    s3_uri_help = "The s3_uri from which to download. "

    api_endpoint_help = (
        "The api_endpoint. Default is https://api.dev.imap-mission.com. "
    )

    parser = argparse.ArgumentParser(prog="imap_api", description=description)

    parser.add_argument("s3_uri", type=str, help=s3_uri_help)
    parser.add_argument("--api_endpoint", type=str, help=api_endpoint_help)
    args = parser.parse_args()

    return args


def download(s3_uri, api_endpoint="https://api.dev.imap-mission.com"):
    """
    Download a file from a given S3 URI via the specified API endpoint.

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
    logging.info(f"Starting download from S3 URI: {s3_uri}")

    url_with_parameters = f"{api_endpoint}/download?{s3_uri}"
    response = requests.get(url_with_parameters)

    # Check if running in Docker
    running_in_docker = os.getenv("DOCKER_ENV") is not None

    # Set the base directory
    base_directory = "/mnt/data" if running_in_docker else "."

    # Join the base directory with the file name
    file_name_and_path = os.path.join(base_directory, s3_uri.replace("s3://", ""))

    download_dir = os.path.dirname(file_name_and_path)
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    with open(file_name_and_path, "wb") as file:
        file.write(response.content)

    logging.info(f"File downloaded and saved to: {file_name_and_path}")

    return file_name_and_path


def upload(local_file_location, api_endpoint="https://api.dev.imap-mission.com"):
    """
    Upload a local file to a remote server using the specified API endpoint.

    Parameters
    ----------
    local_file_location : str
        The file path of the file to be uploaded.
    api_endpoint : str, optional
        The API endpoint to use for uploading the file.
    """
    logging.info(f"Starting upload for file: {local_file_location}")

    remote_file_name = os.path.basename(local_file_location)

    # Modify descriptor in file name
    modified_file_name = remote_file_name.replace(
        remote_file_name[12:15], f"{remote_file_name[12:15]}-test"
    )
    # Upload the file
    url_with_parameters = f"{api_endpoint}/upload?filename={modified_file_name}"
    get_response = requests.get(url_with_parameters)
    upload_url = get_response.json()
    requests.put(upload_url)

    logging.info(f"File uploaded: {modified_file_name}")


def main():
    """Main function for the IMAP API utilities."""
    args = _parse_args()

    if args.api_endpoint is not None:
        file_name_and_path = download(args.s3_uri, args.api_endpoint)
        upload(file_name_and_path, args.api_endpoint)
    else:
        file_name_and_path = download(args.s3_uri)
        upload(file_name_and_path)

    logging.info("Process completed successfully")


if __name__ == "__main__":
    main()
