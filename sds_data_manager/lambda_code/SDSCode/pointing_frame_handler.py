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
    # Each spin is 15 seconds. We want 10 quaternions per spin.
    # duration / # samples (nominally 15/10 = 1.5 seconds)
    step = 5

    # TODO: query .csv for pointing start and end times.
    # TODO: come back to filter nutation and precession in ck.
    et_start = spice.str2et("JUN-01-2025")
    et_end = spice.str2et("JUN-02-2025")
    et_times = np.arange(et_start, et_end, step)

    return et_start, et_end, et_times


def create_pointing_frame(id=-43000):
    """Create the pointing frame.

    Returns
    -------
    kernels : list
        List of kernels.

    References
    ----------
    https://spiceypy.readthedocs.io/en/main/documentation.html.
    spiceypy.spiceypy.ckw02
    """
    mount_path = Path(os.getenv("EFS_MOUNT_PATH"))
    kernels = [str(file) for file in mount_path.iterdir()]

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
        q_avg = eigvecs[:, np.argmax(eigvals)]

        # Get inertial z axis
        z_avg = spice.q2m(list(q_avg))[:, 2]

        # Convert rectangular coordinates to spherical coordinates
        az_z_eclip_list = []
        el_z_eclip_list = []

        for time in z_eclip_time:
            _, az_z_eclip, el_z_eclip = spice.recrad(list(time))
            az_z_eclip_list.append(az_z_eclip)
            el_z_eclip_list.append(el_z_eclip)

        _, az_avg, el_avg = spice.recrad(list(z_avg))

        # Build the DPS frame
        # y_avg is perpendicular to both z_avg and the standard Z-axis
        y_avg = np.cross(z_avg, [0, 0, 1])
        # This calculates the cross product of y_avg and z_avg to get the
        # x-axis, which is perpendicular to both y_avg and z_avg.
        x_avg = np.cross(y_avg, z_avg)

        lv = (
            [et_start, et_end]
            + x_avg.tolist()
            + y_avg.tolist()
            + z_avg.tolist()
            + [0.0, 0.0, 0.0]
        )

        # Open the file and write the data
        with open("dps_data.txt", "w") as fid:
            fid.write(" ".join(map(str, lv)) + "\n")

        # Construct the full command as a single string
        command = "/Users/lasa6858/naif/mice/exe/msopck /Users/lasa6858/imap_processing/imap_processing/dps_frame/dps_setup.txt /Users/lasa6858/imap_processing/imap_processing/dps_frame/dps_data.txt imap_dps.bc"

        # Run the command using shell=True
        result = os.system(command)

        # Calling ckw02
        spice.ckw02(
            handle=spice.ckopn("imap_dps.bc", "SPICE CK", 0),
            begtime=et_start,
            endtime=et_end,
            inst=id,
            ref=frame,
            avflag=avflag,
            segid=seg_id,
            sclkdp=epochs,
            quats=quats,
            avvs=avvs,
            nrec=2,  # Number of records, adjust as needed
        )

        # Close the CK file
        spice.ckcls(spice.ckopn("imap_dps.bc", "SPICE CK", 0))

    return kernels, et_end, et_start
