import logging
from pathlib import Path

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Define the paths
attitude_symlink_path = "/mnt/spice/latest_attitude_kernel.ah.a"
ephemeris_symlink_path = "/mnt/spice/latest_ephemeris_kernel.bsp"


def spice_handler():
    """Read latest spice file.

    This function is a place holder for future L1A or L1B code.
    This function and code is showing that we can read latest
    spice file from EFS through batch job.

    Returns
    -------
    dict
        Status and message
    """

    # Check if the old symlink exists
    if Path(attitude_symlink_path).is_symlink():
        # Path.resolve() returns the absolute path of the symlink
        with open(Path(attitude_symlink_path).resolve()) as f:
            content = f.read()
            logger.info("Attitude kernel:\n%s", content)

    if Path(ephemeris_symlink_path).is_symlink():
        with open(Path(ephemeris_symlink_path).resolve()) as f:
            content = f.read()
            logger.info("Ephemeris kernel:\n%s", content)

    return {"statusCode": 200, "body": "Found symlink"}


if __name__ == "__main__":
    spice_handler()
