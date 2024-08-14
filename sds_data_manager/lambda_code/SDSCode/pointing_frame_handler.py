"""Generate Pointing Frame.

References
----------
https://spiceypy.readthedocs.io/en/main/documentation.html.
"""

import logging
import os
from pathlib import Path

import numpy as np
import spiceypy as spice

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Each spin is 15 seconds. We want 10 quaternions per spin.
# duration / # samples (nominally 15/10 = 1.5 seconds)
STEP = 1.5


def get_coverage(ck_kernel):
    """Create the pointing frame.

    Parameters
    ----------
    ck_kernel : str
        Path of ck_kernel used to create the pointing frame.

    Returns
    -------
    et_start : float
        Start time of ck_kernel.
    et_end : float
        End time of ck_kernel.
    et_times : numpy.ndarray
        Array of times between et_start and et_end.
    """
    # Get the spacecraft ID.
    # https://spiceypy.readthedocs.io/en/main/documentation.html#spiceypy.spiceypy.gipool
    id_imap_spacecraft = spice.gipool("FRAME_IMAP_SPACECRAFT", 0, 1)

    # TODO: Queried pointing start and stop times here.

    # Get the coverage window
    # https://spiceypy.readthedocs.io/en/main/documentation.html#spiceypy.spiceypy.ckcov
    cover = spice.ckcov(ck_kernel, int(id_imap_spacecraft), True, "SEGMENT", 0, "TDB")
    et_start, et_end = spice.wnfetd(cover, 0)
    et_times = np.arange(et_start, et_end, STEP)

    return et_start, et_end, et_times


def average_quaternions(et_times):
    """Average the quarternions.

    Parameters
    ----------
    et_times : numpy.ndarray
        Array of times between et_start and et_end.

    Returns
    -------
    q_avg : np.array
        Average quaternion.
    z_eclip_time : list

    """
    body_quats = []
    z_eclip_time = []
    aggregate = np.zeros((4, 4))

    for tdb in et_times[0:-2]:
        body_rots = spice.pxform("IMAP_SPACECRAFT", "ECLIPJ2000", tdb)
        body_quat = spice.m2q(body_rots)
        body_quats.append(body_quat)
        z_eclip_time.append(body_rots[:, 2])
        if body_quat[0] < 0:
            body_quat = -body_quat
        aggregate += np.outer(body_quat, body_quat)

    # Reference: Claus Gramkow "On Averaging Rotations"
    # Link: https://link.springer.com/content/pdf/10.1023/A:1011129215388.pdf
    aggregate /= len(et_times)

    # Compute eigen values and vectors of the matrix A
    # Eigenvalues tell you how much "influence" each
    # direction (eigenvector) has.
    # The largest eigenvalue corresponds to the direction
    # that has the most influence.
    # The eigenvector corresponding to the largest
    # eigenvalue points in the direction that has the most
    # combined rotation influence.
    eigvals, eigvecs = np.linalg.eig(aggregate)
    # q0: The scalar part of the quaternion.
    # q1, q2, q3: The vector part of the quaternion.
    q_avg = eigvecs[:, np.argmax(eigvals)]

    return q_avg, z_eclip_time


def create_rotation_matrix(et_times):
    q_avg, _ = average_quaternions(et_times)

    # Get inertial z axis
    z_avg = spice.q2m(list(q_avg))[:, 2]

    # Build the DPS frame
    # y_avg is perpendicular to both z_avg and the standard Z-axis
    y_avg = np.cross(z_avg, [0, 0, 1])
    # This calculates the cross product of y_avg and z_avg to get the
    # x-axis, which is perpendicular to both y_avg and z_avg.
    x_avg = np.cross(y_avg, z_avg)

    # Construct the rotation matrix from x_avg, y_avg, z_avg
    rotation_matrix = np.vstack([x_avg, y_avg, z_avg])
    rotation_matrix = np.ascontiguousarray(rotation_matrix)

    return rotation_matrix, z_avg


def create_pointing_frame():
    """Create the pointing frame."""
    mount_path = Path(os.getenv("EFS_MOUNT_PATH"))
    kernels = [str(file) for file in mount_path.iterdir()]
    ck_kernel = [str(file) for file in mount_path.iterdir() if file.suffix == ".bc"]

    with spice.KernelPool(kernels):
        et_start, et_end, et_times = get_coverage(str(ck_kernel[0]))
        rotation_matrix, _ = create_rotation_matrix(et_times)

        # Convert the rotation matrix to a quaternion
        q_avg = spice.m2q(rotation_matrix)

        # TODO: come up with naming convention.
        path_to_imap_dps = mount_path / "imap_dps.bc"

        handle = spice.ckopn(str(path_to_imap_dps), "CK", 0)
        id_imap_sclk = spice.gipool("CK_-43000_SCLK", 0, 1)
        sclk_begtim = spice.sce2c(
            int(id_imap_sclk), et_start
        )  # Convert start time to SCLK
        sclk_endtim = spice.sce2c(int(id_imap_sclk), et_end)

        et_start1 = sclk_begtim
        et_end1 = sclk_endtim
        id_imap_dps = spice.gipool("FRAME_IMAP_DPS", 0, 1)

        spice.ckw02(
            handle,
            et_start1,  # Single start time
            et_end1,  # Single stop time
            int(id_imap_dps),  # Instrument ID
            "ECLIPJ2000",  # Reference frame
            "IMAP_DPS",  # Segment identifier
            1,  # Only one record
            np.array([et_start1]),  # Single start time
            np.array([et_end1]),  # Single stop time
            q_avg,  # quaternion
            np.array([0.0, 0.0, 0.0]),  # Single angular velocity vector
            np.array([1.0]),  # RATES (seconds per tick)
        )

        spice.ckcls(handle)

    return path_to_imap_dps


def lambda_handler(events: dict, context):
    """Lambda handler."""
    logger.info(f"Events: {events}")
    logger.info(f"Context: {context}")

    # Create the pointing frame and save it to EFS
    create_pointing_frame()
