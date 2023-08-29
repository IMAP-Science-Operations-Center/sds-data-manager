import os

import requests

# THESE MUST BE RESET EVERY TIME FOR NOW
UPLOAD_API_URL = "https://6enn3yprkecbvujsf5zsjzsude0borqk.lambda-url.us-west-2.on.aws/"
DOWNLOAD_API_URL = (
    "https://g4iwsrbkdqkm3pj5zm55wg2ufe0jaypw.lambda-url.us-west-2.on.aws/"
)
QUERY_API_URL = "https://ezzc7feb6hlhdrk56x4q23ljnu0ejbkn.lambda-url.us-west-2.on.aws/"


def _execute_api(url, **kwargs):
    query_parameters = []
    for kw in kwargs:
        query_parameters.append(kw + "=" + str(kwargs[kw]))
    query_parameters = "&".join(query_parameters)
    url_with_parameters = url + "?" + query_parameters
    print(url_with_parameters)
    try:
        response = requests.get(url_with_parameters, timeout=10)
    except Exception as e:
        print(f"Could not finish query due to error {e!s}")
        return
    return response


def download(filename, download_dir=""):
    """
    This function is used to download files from the SDS.

    :param filename: The full S3 URI to download
    :param download_dir: The directory on the local machine to download the file to.

    :return: None, but downloads the file to the specified download directory
    """
    download_url = _execute_api(DOWNLOAD_API_URL, s3_uri=filename)

    if download_url.status_code == 400:
        print("Not a valid S3 URI.  Example input: s3://bucket/path/file.ext")
        return
    elif download_url.status_code == 404:
        print("No files were found matching the given URI.")
        return

    file_name_and_path = os.path.join(download_dir, filename[5:])
    download_dir = os.path.dirname(file_name_and_path)
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    with open(file_name_and_path, "wb") as file:
        print(f"Downloading {file_name_and_path}")
        file_location = requests.get(download_url.json()["download_url"], timeout=10)
        file.write(file_location.content)

    return file_location


def query(**kwargs):
    """
    This function is used to query files from the SDS.
    There are no required arguments, the search strings will depend on the mission

    :return: This returns JSON with all information about the files.
    """
    response = _execute_api(QUERY_API_URL, **kwargs)
    return response.json()


def upload(file_location, file_name, **kwargs):
    """
    This function is used to upload files to the SDS.

    :param file_location:
        The path to the file on the local machine to upload to the SDS.
    :param file_name: The name of the file you'd like to upload
    :param kwargs:
        Any additional key word arguments passed into this function are
        stored as tags on the SDS.

    :return: A requests response object. (200 for success)
    """
    response = _execute_api(UPLOAD_API_URL, filename=file_name, **kwargs)

    if response.status_code != 200:
        print(f"Could not generate an upload URL: {response.text}")
        return

    with open(file_location, "rb") as object_file:
        object_text = object_file.read()
    response = requests.put(response.json(), data=object_text, timeout=10)
    return response


if __name__ == "__main__":
    x = download(
        "s3://sds-data-harter-upload-testing/imap/l0/imap_l0_sci_mag_2024_2.pkts"
    )
    print(x)
