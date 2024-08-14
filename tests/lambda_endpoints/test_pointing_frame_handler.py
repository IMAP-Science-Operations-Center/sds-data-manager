import shutil
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import planetmapper
import pytest
import numpy as np
import spiceypy as spice
from planetmapper.kernel_downloader import download_urls

from sds_data_manager.lambda_code.SDSCode.pointing_frame_handler import (
    create_pointing_frame,
    get_coverage,
    create_rotation_matrix,
    average_quaternions,
)


@pytest.fixture()
def kernel_path(tmp_path):
    """Download the required NAIF kernels."""
    planetmapper.set_kernel_path(tmp_path)
    download_urls(
        "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de430.bsp"
    )
    download_urls("https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls")

    test_dir = (
        Path(sys.modules[__name__.split(".")[0]].__file__).parent
        / "test-data"
        / "spice"
    )

    # Format for how the kernels will be stored in EFS.
    shutil.move(
        tmp_path / "naif" / "generic_kernels" / "spk" / "planets" / "de430.bsp",
        tmp_path / "de430.bsp",
    )
    shutil.move(
        tmp_path / "naif" / "generic_kernels" / "lsk" / "naif0012.tls",
        tmp_path / "naif0012.tls",
    )
    shutil.rmtree(tmp_path / "naif")

    for file in test_dir.iterdir():
        if (
            file.name.endswith(".bc")
            or file.name.endswith(".tf")
            or file.name.endswith(".tsc")
        ):
            shutil.copy(file, tmp_path / file.name)

    return tmp_path


@pytest.fixture()
def setup_environment(kernel_path, monkeypatch):
    """Set up environment and create pointing frame."""
    # Set the environment variable
    monkeypatch.setenv("EFS_MOUNT_PATH", str(kernel_path))

    # Prepare kernels and ck_kernel lists
    kernels = [str(file) for file in kernel_path.iterdir()]
    ck_kernel = [
        str(file) for file in kernel_path.iterdir() if file.name == "imap_spin.bc"
    ]

    return kernels, ck_kernel


def test_get_coverage(setup_environment):
    """Tests create_pointing_frame function."""
    kernels, ck_kernel = setup_environment

    with spice.KernelPool(kernels):
        et_start, et_end, et_times = get_coverage(ck_kernel)

    # TODO: Change for queried start/stop times.
    assert et_start == 802008069.184905
    assert et_end == 802094467.184905


def test_create_pointing_frame(monkeypatch, kernel_path):
    """Tests create_pointing_frame function."""
    monkeypatch.setenv("EFS_MOUNT_PATH", str(kernel_path))
    create_pointing_frame()
    kernels = [str(file) for file in kernel_path.iterdir()]
    ck_kernel = [
        str(file) for file in kernel_path.iterdir() if file.name == "imap_spin.bc"
    ]

    with spice.KernelPool(kernels):
        et_start, et_end, et_times = get_coverage(ck_kernel)

        rot1 = spice.pxform("ECLIPJ2000", "IMAP_DPS", et_start + 100)
        rot2 = spice.pxform("ECLIPJ2000", "IMAP_DPS", et_start + 1000)

    assert np.array_equal(rot1, rot2)

    # Nick Dutton's MATLAB code result
    rot1_expected = np.array([[0.0000, 0.0000, 1.0000],
                             [0.9104, -0.4136, 0.0000],
                             [0.4136, 0.9104, 0.0000]])
    np.testing.assert_allclose(rot1, rot1_expected, atol=1e-4)


def test_something(setup_environment):
    """Tests coordinate conversion and visualization."""
    kernels, ck_kernel = setup_environment

    az_z_eclip_list = []
    el_z_eclip_list = []

    with spice.KernelPool(kernels):
        et_start, et_end, et_times = get_coverage(ck_kernel)
        # Create visualization
        q_avg, z_eclip_time = average_quaternions(et_times)
        z_avg_expected = spice.q2m(list(q_avg))[:, 2]
        _, z_avg = create_rotation_matrix(et_times)

        assert z_avg == z_avg_expected

        for time in z_eclip_time:
            _, az_z_eclip, el_z_eclip = spice.recrad(list(time))
            az_z_eclip_list.append(az_z_eclip)
            el_z_eclip_list.append(el_z_eclip)

        _, az_avg, el_avg = spice.recrad(list(z_avg))

    # Plotting for visualization
    plt.figure()

    time_steps = np.arange(et_start, et_end, (et_end - et_start) / len(el_z_eclip_list))

    plt.plot(time_steps, np.array(el_z_eclip_list) * 180 / np.pi, '-b', label='simulated attitude')
    plt.plot(time_steps, np.full(len(time_steps), el_avg * 180 / np.pi), '-r', linewidth=2,
             label='mean z-axis for DPS frame')

    plt.xlabel('Ephemeris Time TDB')
    plt.ylabel('Spacecraft Spin Axis (ecliptic Inertial) Declination')
    plt.legend()

    plt.show()
