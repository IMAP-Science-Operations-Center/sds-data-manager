import logging
from urllib.request import urlopen

logger = logging.getLogger()
logging.basicConfig()
logger.setLevel(logging.INFO)


def download_file(filename_and_path, download_link):
    """This allows user to download file from S3 using pre-signed URL generated
    by the download query API.

    Args:
        filename_and_path (str): exact path with filename where user want to store.
            Eg. dir/subdir/filename.ext
        download_link (str): pre-signed URL from S3
    """
    # Get file content using urlopen
    with urlopen(download_link) as response:
        if response.getcode() != 200:
            logger.warn(
                "Failed to download file [%s], returned status code [%d]",
                download_link,
                response.status_code,
            )
            return
        # save/write file content to file on local machine
        with open(filename_and_path, "wb") as file:
            logger.info(f"Downloading to {filename_and_path}")
            file.write(response.read())
