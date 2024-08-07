"""Generate Pointing Frame."""

import os
from pathlib import Path

import spiceypy as spice

# Set the sampling interval to 5 seconds.
step = 5


def get_coverage():
    """Create the pointing frame.

    Returns
    -------
    et_start : list
        List of dictionary containing the dependency information.
    et_end : list
        List of dictionary containing the dependency information.
    """
    # TODO: query .csv for pointing start and end times.
    # TODO: come back to filter nutation and precession in ck.

    et_start = spice.str2et("JUN-01-2025")
    et_end = spice.str2et("JUN-02-2025")

    return et_start, et_end


def create_pointing_frame():
    """Create the pointing frame.

    Returns
    -------
    kernels : list
        List of kernels.
    """
    mount_path = Path(os.getenv("EFS_MOUNT_PATH"))
    kernels = [file for file in mount_path.iterdir()]

    et_start, et_end = get_coverage()

    print("hi")

    return kernels, et_end, et_start
