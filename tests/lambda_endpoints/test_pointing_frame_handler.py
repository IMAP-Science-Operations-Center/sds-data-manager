"""Tests of Pointing Frame Generation."""

import shutil
import sys
from pathlib import Path

import planetmapper
import pytest
import spiceypy as spice
from planetmapper.kernel_downloader import download_urls

from sds_data_manager.lambda_code.SDSCode.pointing_frame_handler import (
    create_pointing_frame,
    get_coverage,
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


def test_create_pointing_frame(kernel_path, monkeypatch):
    """Tests create_pointing_frame function."""
    # Set the environment variable
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

        print("hi")
