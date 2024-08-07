"""Generate Pointing Frame."""

import os
from pathlib import Path

import numpy as np
import spiceypy as spice


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

    # Set the sampling interval to 5 seconds.
    step = 5

    et_start = spice.str2et("JUN-01-2025")
    et_end = spice.str2et("JUN-02-2025")
    et_times = np.arange(et_start, et_end, step)

    return et_start, et_end, et_times


def create_pointing_frame():
    """Create the pointing frame.

    Returns
    -------
    kernels : list
        List of kernels.
    """
    mount_path = Path(os.getenv("EFS_MOUNT_PATH"))
    kernels = [file for file in mount_path.iterdir()]

    body_quats = []
    z_eclip_time = []
    aggregate = np.zeros((4, 4))

    with spice.KernelPool(kernels):
        et_start, et_end, et_times = get_coverage()

        for tdb in et_times:
            # Rotation matrix from IMAP spacecraft frame to ECLIPJ2000.
            body_rots = spice.pxform("IMAP_SPACECRAFT", "ECLIPJ2000", tdb)
            # Convert rotation matrix to quaternion.
            body_quat = spice.m2q(body_rots)
            body_quats.append(body_quat)
            # z-axis of the IMAP spacecraft frame in the new ECLIPJ2000 frame.
            z_eclip_time.append(body_rots[:, 2])

            # Standardize the quaternion so that they may be compared.
            if body_quat[0] < 0:
                body_quat = -body_quat

            # Aggregate quarternions into a single matrix.
            aggregate += np.outer(body_quat, body_quat)
            print("hi")

    print("hi")

    return kernels, et_end, et_start
