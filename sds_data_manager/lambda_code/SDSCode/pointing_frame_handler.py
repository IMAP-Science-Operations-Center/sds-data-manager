"""Generate Pointing Frame.

Notes
----------
Kernels that are required to run this code:
1. 

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
    # https://spiceypy.readthedocs.io/en/main/documentation.html#spiceypy.spiceypy.wnfetd
    et_start, et_end = spice.wnfetd(cover, 0)
    et_times = np.arange(et_start, et_end, STEP)

    return et_start, et_end, et_times


def average_quaternions(et_times):
    """Average the quaternions.

    Parameters
    ----------
    et_times : numpy.ndarray
        Array of times between et_start and et_end.

    Returns
    -------
    q_avg : np.array
        Average quaternion.
    z_eclip_time : list
        Z-axis of the ECLIPJ2000 frame. Used for plotting.
    """
    body_quats = []
    z_eclip_time = []
    aggregate = np.zeros((4, 4))

    for tdb in et_times:
        # Rotation matrix from IMAP spacecraft frame to ECLIPJ2000.
        # https://spiceypy.readthedocs.io/en/main/documentation.html#spiceypy.spiceypy.pxform
        body_rots = spice.pxform("IMAP_SPACECRAFT", "ECLIPJ2000", tdb)
        # Convert rotation matrix to quaternion.
        # https://spiceypy.readthedocs.io/en/main/documentation.html#spiceypy.spiceypy.m2q
        body_quat = spice.m2q(body_rots)
        body_quats.append(body_quat)
        # z-axis of the ECLIPJ2000 frame.
        z_eclip_time.append(body_rots[:, 2])

        # Standardize the quaternion so that they may be compared.
        if body_quat[0] < 0:
            body_quat = -body_quat

        # Aggregate quaternions into a single matrix.
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
    """Create a rotation matrix.

    Parameters
    ----------
    et_times : numpy.ndarray
        Array of times between et_start and et_end.

    Returns
    -------
    rotation_matrix : np.array
        Rotation matrix.
    z_avg : np.array
        Inertial z axis. Used for plotting.
    """
    # Averaged quaternions.
    q_avg, _ = average_quaternions(et_times)

    # TODO: Ask Nick if this part is necessary.
    # Converts the averaged quaternion (q_avg) into a rotation matrix
    # and get inertial z axis.
    # https://spiceypy.readthedocs.io/en/main/documentation.html#spiceypy.spiceypy.q2m
    z_avg = spice.q2m(list(q_avg))[:, 2]
    # y_avg is perpendicular to both z_avg and the standard Z-axis.
    y_avg = np.cross(z_avg, [0, 0, 1])
    # x_avg is perpendicular to y_avg and z_avg.
    x_avg = np.cross(y_avg, z_avg)

    # Construct the rotation matrix from x_avg, y_avg, z_avg
    rotation_matrix = np.array([x_avg, y_avg, z_avg])

    return rotation_matrix, z_avg


def create_pointing_frame():
    """Create the pointing frame.

    Returns
    -------
    path_to_imap_dps : str
        Path to dps frame.
    """
    # Mount path to EFS.
    mount_path = Path(os.getenv("EFS_MOUNT_PATH"))

    # TODO: this part will change with ensure_spice decorator.
    kernels = [str(file) for file in mount_path.iterdir()]
    ck_kernel = [str(file) for file in mount_path.iterdir()
                 if file.suffix == ".bc"]

    # Furnish the kernels.
    with spice.KernelPool(kernels):

        # Get timerange for the pointing frame kernel.
        et_start, et_end, et_times = get_coverage(str(ck_kernel[0]))
        # Create a rotation matrix
        rotation_matrix, _ = create_rotation_matrix(et_times)

        # Convert the rotation matrix to a quaternion.
        # https://spiceypy.readthedocs.io/en/main/documentation.html#spiceypy.spiceypy.m2q
        q_avg = spice.m2q(rotation_matrix)

        # TODO: come up with naming convention.
        path_to_imap_dps = mount_path / "imap_dps.bc"

        # Open a new CK file, returning the handle of the opened file.
        # https://spiceypy.readthedocs.io/en/main/documentation.html#spiceypy.spiceypy.ckopn
        handle = spice.ckopn(str(path_to_imap_dps), "CK", 0)
        # Get the SCLK ID.
        # https://spiceypy.readthedocs.io/en/main/documentation.html#spiceypy.spiceypy.gipool
        id_imap_sclk = spice.gipool("CK_-43000_SCLK", 0, 1)
        # https://spiceypy.readthedocs.io/en/main/documentation.html#spiceypy.spiceypy.sce2c
        # Convert start and end times to SCLK.
        sclk_begtim = spice.sce2c(int(id_imap_sclk), et_start)
        sclk_endtim = spice.sce2c(int(id_imap_sclk), et_end)

        # Get the pointing frame ID.
        # https://spiceypy.readthedocs.io/en/main/documentation.html#spiceypy.spiceypy.gipool
        id_imap_dps = spice.gipool("FRAME_IMAP_DPS", 0, 1)

        spice.ckw02(
            handle,
            sclk_begtim,  # Single start time
            sclk_endtim,  # Single stop time
            int(id_imap_dps),  # Instrument ID
            "ECLIPJ2000",  # Reference frame
            "IMAP_DPS",  # Segment identifier
            1,  # Only one record
            np.array([sclk_begtim]),  # Single start time
            np.array([sclk_endtim]),  # Single stop time
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
