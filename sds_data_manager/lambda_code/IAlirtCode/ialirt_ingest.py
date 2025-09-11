"""IALiRT ingest lambda."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import boto3
import botocore
import imap_data_access
import numpy as np
import pandas as pd
import requests
import spiceypy
import xarray as xr
from boto3.dynamodb.conditions import Key
from imap_data_access.processing_input import (
    AncillaryInput,
    ProcessingInputCollection,
    SPICEInput,
    SPICESource,
)
from imap_processing import imap_module_directory
from imap_processing.cdf.utils import load_cdf
from imap_processing.ialirt.l0.parse_mag import process_packet
from imap_processing.ialirt.l0.process_codice import process_codice
from imap_processing.ialirt.l0.process_hit import process_hit
from imap_processing.ialirt.l0.process_swapi import process_swapi_ialirt
from imap_processing.ialirt.l0.process_swe import process_swe
from imap_processing.mag.l1b.mag_l1b import MagAncillaryCombiner
from imap_processing.spice.geometry import (
    SpiceBody,
    SpiceFrame,
    imap_state,
)
from imap_processing.spice.time import met_to_sclkticks, met_to_utc, sct_to_et
from imap_processing.utils import packet_file_to_datasets

"""Functions to support I-ALiRT MAG packet parsing."""

import logging
from decimal import Decimal

import numpy as np
import xarray as xr

from imap_processing.ialirt.l0.ialirt_spice import (
    transform_instrument_vectors_to_inertial,
)
from imap_processing.ialirt.l0.mag_l0_ialirt_data import (
    Packet0,
    Packet1,
    Packet2,
    Packet3,
)
from imap_processing.ialirt.utils.grouping import find_groups
from imap_processing.ialirt.utils.time import calculate_time
from imap_processing.mag.l1a.mag_l1a_data import TimeTuple
from imap_processing.mag.l1b.mag_l1b import (
    calibrate_vector,
    shift_time,
)
from imap_processing.mag.l1d.mag_l1d_data import MagL1d
from imap_processing.mag.l2.mag_l2_data import MagL2L1dBase
from imap_processing.spice.geometry import (
    SpiceFrame,
    cartesian_to_spherical,
    frame_transform,
    spherical_to_cartesian,
)
from imap_processing.spice.time import met_to_ttj2000ns, met_to_utc, ttj2000ns_to_et

logger = logging.getLogger(__name__)


def get_pkt_counter(status_values: xr.DataArray) -> xr.DataArray:
    """
    Get the packet counters.

    Parameters
    ----------
    status_values : xr.DataArray
        Status data.

    Returns
    -------
    pkt_counters : xr.DataArray
        Packet counters.
    """
    # mag_status is a 24 bit unsigned field
    # The leading 2 bits of STATUS are a 2 bit 0-3 counter
    pkt_counter = (status_values >> 22) & 0x03

    return pkt_counter


def get_status_data(status_values: xr.DataArray, pkt_counters: xr.DataArray) -> dict:
    """
    Get the status data.

    Parameters
    ----------
    status_values : xr.DataArray
        Status data.
    pkt_counters : xr.DataArray
        Packet counters.

    Returns
    -------
    combined_packets : dict
        Decoded packets.
    """
    decoders = {
        0: Packet0,
        1: Packet1,
        2: Packet2,
        3: Packet3,
    }

    combined_packets = {}

    for pkt_num, decoder in decoders.items():
        status_subset = status_values[pkt_counters == pkt_num]
        decoded_packet = decoder(int(status_subset))
        combined_packets.update(vars(decoded_packet))

    return combined_packets


def get_bytes(val: int) -> list[int]:
    """
    Extract three bytes from a 24-bit integer.

    Parameters
    ----------
    val : int
        24-bit integer value.

    Returns
    -------
    list[int]
        List of three extracted bytes.
    """
    return [
        (val >> 16) & 0xFF,  # Most significant byte (Byte2)
        (val >> 8) & 0xFF,  # Middle byte (Byte1)
        (val >> 0) & 0xFF,  # Least significant byte (Byte0)
    ]


def extract_magnetic_vectors(science_values: xr.DataArray) -> dict:
    """
    Extract the magnetic vectors.

    Parameters
    ----------
    science_values : xr.DataArray
        Science data.

    Returns
    -------
    vectors : dict
        Magnetic vectors.
    """
    # Primary sensor:
    pri_x: np.int16 = np.uint16((int(science_values[0]) >> 8) & 0xFFFF).astype(np.int16)
    pri_y: np.int16 = np.uint16(
        ((int(science_values[0]) << 8) & 0xFF00)
        | ((int(science_values[1]) >> 16) & 0xFF)
    ).astype(np.int16)
    pri_z: np.int16 = np.uint16(int(science_values[1]) & 0xFFFF).astype(np.int16)

    # Secondary sensor:
    sec_x: np.int16 = np.uint16((int(science_values[2]) >> 8) & 0xFFFF).astype(np.int16)
    sec_y: np.int16 = np.uint16(
        ((int(science_values[2]) << 8) & 0xFF00)
        | ((int(science_values[3]) >> 16) & 0xFF)
    ).astype(np.int16)

    sec_z: np.int16 = np.uint16(int(science_values[3]) & 0xFFFF).astype(np.int16)

    vectors = {
        "pri_x": pri_x,
        "pri_y": pri_y,
        "pri_z": pri_z,
        "sec_x": sec_x,
        "sec_y": sec_y,
        "sec_z": sec_z,
    }

    return vectors


def get_time(
    grouped_data: xr.Dataset,
    group: int,
    pkt_counter: xr.DataArray,
    time_shift_mago: xr.DataArray,
    time_shift_magi: xr.DataArray,
) -> dict:
    """
    Get the time for the grouped data.

    Parameters
    ----------
    grouped_data : xr.Dataset
        Grouped data.
    group : int
        Group number.
    pkt_counter : xr.DataArray
        Packet counter.
    time_shift_mago : xr.DataArray
        Time shift value mago.
    time_shift_magi : xr.DataArray
        Time shift value magi.

    Returns
    -------
    time_data : dict
        Coarse and fine time for Primary and Secondary Sensors.

    Notes
    -----
    Packet id 0 is course and fine time for the primary sensor PRI.
    Packet id 2 is the course time for the secondary sensor SEC.
    """
    # Get the coarse and fine time for the primary and secondary sensors.
    pri_coarsetm = grouped_data["mag_acq_tm_coarse"][
        (grouped_data["group"] == group).values
    ][pkt_counter == 0]

    pri_fintm = grouped_data["mag_acq_tm_fine"][
        (grouped_data["group"] == group).values
    ][pkt_counter == 0]

    sec_coarsetm = grouped_data["mag_acq_tm_coarse"][
        (grouped_data["group"] == group).values
    ][pkt_counter == 2]

    sec_fintm = grouped_data["mag_acq_tm_fine"][
        (grouped_data["group"] == group).values
    ][pkt_counter == 2]

    time_data: dict[str, int | float] = {
        "pri_coarsetm": int(pri_coarsetm.item()),
        "pri_fintm": int(pri_fintm.item()),
        "sec_coarsetm": int(sec_coarsetm.item()),
        "sec_fintm": int(sec_fintm.item()),
    }

    primary_time = TimeTuple(int(pri_coarsetm.item()), int(pri_fintm.item()))
    secondary_time = TimeTuple(int(sec_coarsetm.item()), int(sec_fintm.item()))
    time_data_pri_met = primary_time.to_seconds()
    time_data_primary_ttj2000ns = met_to_ttj2000ns(time_data_pri_met)
    time_data["primary_epoch"] = shift_time(
        time_data_primary_ttj2000ns, time_shift_mago
    )
    time_data_sec_met = secondary_time.to_seconds()
    time_data_secondary_ttj2000ns = met_to_ttj2000ns(time_data_sec_met)
    time_data["secondary_epoch"] = shift_time(
        time_data_secondary_ttj2000ns, time_shift_magi
    )

    return time_data


def calculate_l1b(
    grouped_data: xr.Dataset,
    group: int,
    pkt_counter: xr.DataArray,
    science_data: dict,
    status_data: dict,
    calibration_dataset: xr.Dataset,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """
    Calculate equivalent of l1b data product.

    Parameters
    ----------
    grouped_data : xr.Dataset
        Grouped data.
    group : int
        Group number.
    pkt_counter : xr.DataArray
        Packet counter.
    science_data : dict
        Science data.
    status_data : dict
        Status data.
    calibration_dataset : xr.Dataset
        Calibration dataset.

    Returns
    -------
    updated_vector_mago : numpy.ndarray
        Calibrated mago vector.
    updated_vector_magi : numpy.ndarray
        Calibrated magi vector.
    time_data : dict
        Time data.
    """
    calibration_matrix_mago, time_shift_mago = (
        retrieve_matrix_from_single_l1b_calibration(calibration_dataset, is_mago=True)
    )
    calibration_matrix_magi, time_shift_magi = (
        retrieve_matrix_from_single_l1b_calibration(calibration_dataset, is_mago=False)
    )

    logger.info(f"calibration_matrix_mago shape: {calibration_matrix_mago.shape}.")
    logger.info(f"calibration_matrix_magi shape: {calibration_matrix_magi.shape}.")

    # Get time values for each group.
    time_data = get_time(
        grouped_data, group, pkt_counter, time_shift_mago, time_shift_magi
    )

    input_vector_mago = np.array(
        [
            science_data["pri_x"],
            science_data["pri_y"],
            science_data["pri_z"],
            status_data["fob_range"],
        ]
    )
    input_vector_magi = np.array(
        [
            science_data["sec_x"],
            science_data["sec_y"],
            science_data["sec_z"],
            status_data["fib_range"],
        ]
    )

    updated_vector_mago = calibrate_vector(input_vector_mago, calibration_matrix_mago)
    updated_vector_magi = calibrate_vector(input_vector_magi, calibration_matrix_magi)

    return updated_vector_mago, updated_vector_magi, time_data


def calibrate_and_offset_vectors(
    vectors: np.ndarray,
    calibration: np.ndarray,
    offsets: np.ndarray,
    is_magi: bool = False,
) -> np.ndarray:
    """
    Apply calibration and offsets to magnetic vectors.

    Parameters
    ----------
    vectors : np.ndarray
        Raw magnetic vectors, shape (n, 4).
    calibration : np.ndarray
        Calibration matrix, shape (3, 3, 4).
    offsets : np.ndarray
        Offsets array, shape (2, 4, 3) where:
        - index 0 = MAGo, 1 = MAGi
        - second index = range (0–3)
        - third index = axis (x, y, z)
    is_magi : bool, optional
        True if applying to MAGi data, False for MAGo.

    Returns
    -------
    calibrated_and_offset_vectors : np.ndarray
        Calibrated and offset vectors, shape (n, 3).
    """
    # Apply calibration matrix -> (n,4)
    # apply_calibration_offset_single_vector
    calibrated = MagL2L1dBase.apply_calibration(vectors.reshape(1, 4), calibration)

    # Apply offsets per vector
    # vec shape (4)
    # offsets shape (2, 4, 3) where first index is 0 for MAGo and 1 for MAGi
    calibrated = np.array(
        [
            MagL1d.apply_calibration_offset_single_vector(vec, offsets, is_magi=is_magi)
            for vec in calibrated
        ]
    )

    return calibrated[:, :3]


def apply_gradiometry_correction(
    mago_vectors_eclipj2000: np.ndarray,
    mago_time_data: np.ndarray,
    magi_vectors_eclipj2000: np.ndarray,
    magi_time_data: np.ndarray,
    gradiometer_factor: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Align MAGi to MAGo timestamps and apply gradiometry correction.

    Parameters
    ----------
    mago_vectors_eclipj2000 : np.ndarray
        MAGo vectors in inertial frame, shape (N, 3).
    mago_time_data : np.ndarray
        Time for primary sensor, shape (N, 3).
    magi_vectors_eclipj2000 : np.ndarray
        MAGi vectors in inertial frame, shape (M, 3).
    magi_time_data : np.ndarray
        Time for secondary sensor, shape (N, 3).
    gradiometer_factor : np.ndarray
        A (3,3) element matrix to scale and rotate the gradiometer offsets.

    Returns
    -------
    mago_corrected : np.ndarray
        Corrected MAGo vectors in inertial frame, shape (N, 3).
    magnitude : np.ndarray
        Magnitude of corrected MAGo vectors, shape (N,).
    """
    gradiometry_offsets = MagL1d.calculate_gradiometry_offsets(
        mago_vectors_eclipj2000,
        mago_time_data,
        magi_vectors_eclipj2000,
        magi_time_data,
    )
    mago_corrected = MagL1d.apply_gradiometry_offsets(
        gradiometry_offsets, mago_vectors_eclipj2000, gradiometer_factor
    )
    magnitude = np.linalg.norm(mago_corrected, axis=-1).squeeze()

    return mago_corrected, magnitude


def transform_to_inertial(
    sc_spin_phase_rad: np.ndarray,
    sc_inertial_right: np.ndarray,
    sc_inertial_decline: np.ndarray,
    attitude_time: np.ndarray,
    target_time: float,
    mag_vector: np.ndarray,
    instrument_frame: SpiceFrame,
) -> np.ndarray:
    """
    Transform vector to ECLIPJ2000.

    Parameters
    ----------
    sc_spin_phase_rad : numpy.ndarray
        Spin phase for 4 packets 0 to 2π radians, shape (4).
    sc_inertial_right : numpy.ndarray
        Inertial right ascension for 4 packets 0 to 2π radians, shape (4).
    sc_inertial_decline : numpy.ndarray
        Inertial declination for 4 packets -π/2 to π/2 radians, shape (4).
    attitude_time : np.ndarray
        Timestamps for the 4 packets.
        Example: test_met = grouped_data["met"][
                 (grouped_data["group"] == group).values].
        ttj2000ns = met_to_ttj2000ns(test_met.values).
    target_time : float
        Time at which to apply the transformation.
        Will be primary_epoch (mago vector) or secondary_epoch (magi vector).
        Example: time_data['primary_epoch'].
    mag_vector : numpy.ndarray
        Vector, shape (3).
    instrument_frame : SpiceFrame
        SPICE frame of the instrument.

    Returns
    -------
    inertial_vector : np.ndarray
        Transformed vector in the ECLIPJ2000 frame, shape (3,).

    Notes
    -----
    The MAG vectors are calculated based on 4 packets,
    each of which contains its own spin phase,
    inertial right ascension, and inertial decline.
    """
    if target_time < attitude_time.min() or target_time > attitude_time.max():
        logger.warning(
            f"target_time {target_time} is outside attitude_time bounds "
            f"[{attitude_time.min()}, {attitude_time.max()}]; using edge values."
        )

    # Get sort order based on attitude_time
    sort_idx = np.argsort(attitude_time)

    # Sort all arrays accordingly
    attitude_time = attitude_time[sort_idx]
    sc_spin_phase_rad = sc_spin_phase_rad[sort_idx]
    sc_inertial_right = sc_inertial_right[sort_idx]
    sc_inertial_decline = sc_inertial_decline[sort_idx]

    # Interpolate spin phase, RA, and Dec at target_time
    # Convert RA/Dec to unit cartesian vectors
    spherical_coords = np.stack(
        [
            np.ones_like(sc_inertial_right),
            np.degrees(sc_inertial_right),
            np.degrees(sc_inertial_decline),
        ],
        axis=-1,
    )
    vecs = spherical_to_cartesian(spherical_coords)

    # Interpolate in Cartesian space
    vx = np.interp(target_time, attitude_time, vecs[:, 0])
    vy = np.interp(target_time, attitude_time, vecs[:, 1])
    vz = np.interp(target_time, attitude_time, vecs[:, 2])
    v_interp = np.array([vx, vy, vz])
    # Normalize vector so that its magnitude is 1.
    v_interp /= np.linalg.norm(v_interp)

    # Convert back to spherical
    ra_dec = cartesian_to_spherical(v_interp)
    ra_deg = ra_dec[1]
    dec_deg = ra_dec[2]

    # Account for discontinuities in spin phase.
    spin_phase_unwrapped = np.unwrap(sc_spin_phase_rad)
    spin_phase_interp = np.interp(target_time, attitude_time, spin_phase_unwrapped)
    spin_phase_deg = np.degrees(spin_phase_interp) % 360

    # Transform each into ECLIPJ2000
    inertial_vector = transform_instrument_vectors_to_inertial(
        np.asarray(mag_vector).reshape(1, 3),
        np.array([spin_phase_deg]),
        np.array([ra_deg]),
        np.array([dec_deg]),
        instrument_frame,
    )[0]

    return inertial_vector


def transform_to_frames(
    target_time: np.ndarray,
    inertial_vector: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Transform vector to different frames.

    Parameters
    ----------
    target_time : np.ndarray
        Time at which to apply the transformation.
        Will be primary_epoch (mago vector).
        Example: time_data['primary_epoch'].
    inertial_vector : np.ndarray
        Transformed vector in the ECLIPJ2000 frame, shape (3,).

    Returns
    -------
    gse_vector : np.ndarray
        Transformed vector in the GSE frame, shape (3,).
    gsm_vector : np.ndarray
        Transformed vector in the GSM frame, shape (3,).
    rtn_vector : np.ndarray
        Transformed vector in the RTN frame, shape (3,).
    """
    et_target_time = ttj2000ns_to_et(target_time)

    gse_vector = frame_transform(
        et_target_time, inertial_vector, SpiceFrame.ECLIPJ2000, SpiceFrame.IMAP_GSE
    )
    gsm_vector = frame_transform(
        et_target_time, inertial_vector, SpiceFrame.ECLIPJ2000, SpiceFrame.IMAP_GSM
    )
    rtn_vector = frame_transform(
        et_target_time, inertial_vector, SpiceFrame.ECLIPJ2000, SpiceFrame.IMAP_RTN
    )

    return gse_vector, gsm_vector, rtn_vector


def process_packet(
    accumulated_data: xr.Dataset,
    engineering_calibration_dataset: xr.Dataset,
    l1d_calibration_dataset: xr.Dataset,
) -> list[dict]:
    """
    Parse the MAG packets.

    Parameters
    ----------
    accumulated_data : xr.Dataset
        Packets dataset accumulated over 1 min.
    engineering_calibration_dataset : xr.Dataset
        Engineering calibration dataset.
    l1d_calibration_dataset : xr.Dataset
        L1D calibration dataset.

    Returns
    -------
    mag_data : list[dict]
        Dictionaries of the parsed data product.
    """
    logger.info(
        f"Parsing MAG for time: {accumulated_data['mag_acq_tm_coarse'].min().values} - "
        f"{accumulated_data['mag_acq_tm_coarse'].max().values}."
    )

    # Subsecond time conversion specified in 7516-9054 GSW-FSW ICD.
    # Value of SCLK subseconds, unsigned, (LSB = 1/256 sec)
    met = calculate_time(
        accumulated_data["sc_sclk_sec"], accumulated_data["sc_sclk_sub_sec"], 256
    )

    # Add required parameters.
    accumulated_data["met"] = met
    pkt_counter = get_pkt_counter(accumulated_data["mag_status"])
    accumulated_data["pkt_counter"] = pkt_counter

    grouped_data = find_groups(accumulated_data, (0, 3), "pkt_counter", "met")

    unique_groups = np.unique(grouped_data["group"])
    mag_data = []
    met_all = []
    mago_vectors_all = []
    mago_times_all = []
    magi_vectors_all = []
    magi_times_all = []
    incomplete_groups = []

    for group in unique_groups:
        # Get status values for each group.
        status_values = grouped_data["mag_status"][
            (grouped_data["group"] == group).values
        ]
        pkt_counter = grouped_data["pkt_counter"][
            (grouped_data["group"] == group).values
        ]

        if not np.array_equal(pkt_counter, np.arange(4)):
            incomplete_groups.append(group)
            continue

        # Get decoded status data.
        status_data = get_status_data(status_values, pkt_counter)

        if status_data["pri_isvalid"] == 0 and status_data["sec_isvalid"] == 0:
            logger.info(f"Group {group} contains no valid data for either sensor.")
            continue

        # Get science values for each group.
        science_values = grouped_data["mag_data"][
            (grouped_data["group"] == group).values
        ]
        science_data = extract_magnetic_vectors(science_values)
        updated_vector_mago, updated_vector_magi, time_data = calculate_l1b(
            grouped_data,
            group,
            pkt_counter,
            science_data,
            status_data,
            engineering_calibration_dataset,
        )

        # Note: primary = MAGo, secondary = MAGi.
        # Populate with a FILL value if either sensor is invalid,
        # but not both.
        if status_data["pri_isvalid"] == 0:
            updated_vector_mago = np.full(4, -32768)
        if status_data["sec_isvalid"] == 0:
            updated_vector_magi = np.full(4, -32768)

        mago_calibration = l1d_calibration_dataset["URFTOORFO"][0]
        magi_calibration = l1d_calibration_dataset["URFTOORFI"][0]
        offsets = l1d_calibration_dataset["offsets"][0]

        mago_out = calibrate_and_offset_vectors(
            updated_vector_mago, mago_calibration, offsets, is_magi=False
        )
        magi_out = calibrate_and_offset_vectors(
            updated_vector_magi, magi_calibration, offsets, is_magi=True
        )
        sc_spin_phase_rad = grouped_data["sc_spin_phase"][
            (grouped_data["group"] == group).values
        ]
        sc_inertial_right = grouped_data["sc_inertial_right"][
            (grouped_data["group"] == group).values
        ]
        sc_inertial_decline = grouped_data["sc_inertial_decline"][
            (grouped_data["group"] == group).values
        ]

        attitude_time = met_to_ttj2000ns(
            grouped_data["met"][(grouped_data["group"] == group).values]
        )

        # Convert to ECLIPJ2000 frame.
        mago_inertial_vector = transform_to_inertial(
            sc_spin_phase_rad.values,
            sc_inertial_right.values,
            sc_inertial_decline.values,
            attitude_time,
            time_data["primary_epoch"],
            mago_out,
            SpiceFrame.IMAP_MAG_O,
        )
        magi_inertial_vector = transform_to_inertial(
            sc_spin_phase_rad.values,
            sc_inertial_right.values,
            sc_inertial_decline.values,
            attitude_time,
            time_data["secondary_epoch"],
            magi_out,
            SpiceFrame.IMAP_MAG_I,
        )

        met = grouped_data["met"][(grouped_data["group"] == group).values]
        met_all.append(met.values[0])
        mago_times_all.append(time_data["primary_epoch"])
        mago_vectors_all.append(mago_inertial_vector)
        magi_vectors_all.append(magi_inertial_vector)
        magi_times_all.append(time_data["secondary_epoch"])

    if incomplete_groups:
        logger.info(
            f"The following mag groups were skipped due to "
            f"missing or duplicate pkt_counter values: "
            f"{incomplete_groups}"
        )

    mago_corrected, magnitude = apply_gradiometry_correction(
        np.array(mago_vectors_all),
        np.array(mago_times_all),
        np.array(magi_vectors_all),
        np.array(magi_times_all),
        l1d_calibration_dataset["gradiometer_factor"].values.squeeze(),
    )

    gse_vector, gsm_vector, rtn_vector = transform_to_frames(
        np.array(mago_times_all), mago_corrected
    )

    spherical = cartesian_to_spherical(gsm_vector)
    phi_gsm = spherical[:, 1]
    theta_gsm = spherical[:, 2]

    spherical = cartesian_to_spherical(gse_vector)
    phi_gse = spherical[:, 1]
    theta_gse = spherical[:, 2]

    # Omit the first value since we expect it to be extrapolated.
    for i in range(len(mago_corrected)):
        if i == 0:
            continue

        mag_data.append(
            {
                "apid": 478,
                "met": int(met_all[i]),
                "met_in_utc": met_to_utc(met_all[i]).split(".")[0],
                "ttj2000ns": int(met_to_ttj2000ns(met_all[i])),
                "mag_epoch": int(mago_times_all[i]),
                "mag_B_GSE": [Decimal(str(v)) for v in gse_vector[i]],
                "mag_B_GSM": [Decimal(str(v)) for v in gsm_vector[i]],
                "mag_B_RTN": [Decimal(str(v)) for v in rtn_vector[i]],
                "mag_B_magnitude": Decimal(str(magnitude[i])),
                "mag_phi_B_GSM": Decimal(str(phi_gsm[i])),
                "mag_theta_B_GSM": Decimal(str(theta_gsm[i])),
                "mag_phi_B_GSE": Decimal(str(phi_gse[i])),
                "mag_theta_B_GSE": Decimal(str(theta_gse[i])),
            }
        )

    return mag_data


def retrieve_matrix_from_single_l1b_calibration(
    calibration_dataset: xr.Dataset, is_mago: bool = True
) -> tuple[xr.DataArray, xr.DataArray]:
    """
    Retrieve the calibration matrix and time shift from the calibration dataset.

    Parameters
    ----------
    calibration_dataset : xarray.Dataset
        The calibration dataset containing the calibration matrices and time shift.
    is_mago : bool
        Whether the calibration is for mago or magi. If True, it retrieves the mago
        calibration matrix and time shift. If False, it retrieves the magi calibration
        matrix and time shift.

    Returns
    -------
    tuple[xr.DataArray, xr.DataArray]
        The calibration matrix and time shift. These can be passed directly into
        update_vector, calibrate_vector, and shift_time.
    """
    if is_mago:
        calibration_matrix = calibration_dataset["MFOTOURFO"].squeeze("epoch")
        time_shift = calibration_dataset["OTS"].squeeze("epoch")
    else:
        calibration_matrix = calibration_dataset["MFITOURFI"].squeeze("epoch")
        time_shift = calibration_dataset["ITS"].squeeze("epoch")

    return calibration_matrix, time_shift


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

KERNELS = {
    "ephemeris_predicted",
    "ephemeris_90days",
    "planetary_ephemeris",
    "spacecraft_clock",
    "leapseconds",
    "imap_frames",
    "science_frames",
    "planetary_constants",
}
EFS_BASE_PATH = Path("/mnt/data")


def get_ancillary(instrument, descriptor):
    """Query and download ancillary data if not already present.

    Parameters
    ----------
    instrument : str
        The name of the instrument.
    descriptor : str
        The name of the descriptor.

    Returns
    -------
    download_path : Path
        Download path of calibration file.
    """
    imap_data_access.config["DATA_DIR"] = EFS_BASE_PATH
    calibration_files = imap_data_access.query(
        table="ancillary",
        instrument=instrument,
        descriptor=descriptor,
        version="latest",
    )

    if not calibration_files:
        raise FileNotFoundError(
            f"No calibration file found for {instrument=}, {descriptor=}"
        )

    calibration_file = calibration_files[0]

    download_path = imap_data_access.download(calibration_file["file_path"])
    logger.info(f"Adding to {download_path} to calibration files.")

    return download_path


def get_latest_spice_kernels(url: str) -> ProcessingInputCollection:
    """Query the SPICE metakernel API for latest SPICE kernel filenames.

    Parameters
    ----------
    url: str
        AWS account name.

    Returns
    -------
    dependency_inputs: ProcessingInputCollection
        A collection containing a SPICEInput object with the list of kernel filenames
        returned from the metakernel API.
    """
    dependency_inputs = ProcessingInputCollection()

    now = datetime.now(timezone.utc)
    one_week_ago = now - timedelta(weeks=1)
    # Define J2000 epoch: 2000-01-01T12:00:00 UTC
    # TODO: remove this once Bryan changes takes in 'yyyymmdd' format
    j2000 = datetime(2000, 1, 1, 11, 58, 56, tzinfo=timezone.utc)
    et_end_time = (now - j2000).total_seconds()
    et_start_time = (one_week_ago - j2000).total_seconds()

    file_types = ",".join(KERNELS)
    #url = 'https://api.dev.imap-mission.com'
    metakernel_url = url + "/metakernel"

    params = {
        "start_time": str(int(et_start_time)),
        "end_time": str(int(et_end_time)),
        "list_files": "True",
        "file_types": file_types,
    }

    logger.info(f"Sending request to {metakernel_url} with params: {params}")
    response = requests.get(metakernel_url, params=params, timeout=10)
    metakernel_files = response.json()

    logger.info(f"Found metakernel files: {metakernel_files}. Adding to collection.")
    dependency_inputs.add(SPICEInput(*metakernel_files))

    return dependency_inputs


def download_spice_file(dependencies) -> list[Path]:
    """Download SPICE kernel files from the IMAP data archive and store them in EFS.

    Parameters
    ----------
    dependencies: ProcessingInputCollection
        A collection containing a SPICEInput object with the list of kernel filenames
        returned from the metakernel API.

    Returns
    -------
    spice_files: list[Path]
        A list of Path objects representing the SPICE files stored in EFS.

    Notes
    -----
    List is priority ordered so furnishing in order results in correct SPICE priority.
    """
    imap_data_access.config["DATA_DIR"] = EFS_BASE_PATH
    print("Fourth attempt")
    dependencies.download_all_files()

    spice_files = dependencies.get_file_paths(data_type=SPICESource.SPICE.value)
    logger.info(f"Downloaded SPICE files: {spice_files}. Furnishing kernels.")
    spiceypy.furnsh([str(file.resolve()) for file in spice_files])

    return spice_files


def query_filenames(bucket: str, region: str, now: datetime):
    """Query the packets in the s3 bucket.

    Parameters
    ----------
    bucket : str
        The name of the S3 bucket.
    region : str
        The region in which the s3 bucket resides.
    now : datetime
        The current time in UTC.

    Returns
    -------
    filenames : list
        List of file paths.
    """
    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    five_minutes_ago = now - timedelta(minutes=5)

    # Account for any cases in which data spans a threshold since
    # s3 only uses prefixes for queries.
    # Example:
    # now = 2026-01-01T00:02:00Z
    # five_minutes_ago = 2025-12-31T23:57:00Z
    first_prefix = five_minutes_ago.strftime("packets/iois_1_packets_%Y_%j_%H_")
    second_prefix = now.strftime("packets/iois_1_packets_%Y_%j_%H_")

    first_response = s3_client.list_objects_v2(Bucket=bucket, Prefix=first_prefix)
    objects = first_response.get("Contents", [])

    if second_prefix != first_prefix:
        second_response = s3_client.list_objects_v2(Bucket=bucket, Prefix=second_prefix)
        objects.extend(second_response.get("Contents", []))

    filenames = []
    for obj in objects:
        key = obj["Key"]
        timestamp_str = key.split("iois_1_packets_")[1]
        timestamp_str = timestamp_str.removesuffix(".bin")
        timestamp = datetime.strptime(timestamp_str, "%Y_%j_%H_%M_%S")
        timestamp = timestamp.replace(tzinfo=timezone.utc)

        if five_minutes_ago <= timestamp <= now:
            filenames.append(key)

    return filenames


def parse_packets(filenames: list, bucket: str, download_dir: Path, apid=478):
    """Get packets into datasets and combine.

    This function is an event handler for s3 ingest bucket.
    It is also used to ingest data to the DynamoDB table.

    Parameters
    ----------
    filenames : list
        List of file paths.
    bucket : str
        The name of the S3 bucket.
    download_dir : Path
        The directory where the file will be downloaded.
    apid : int
        The apid of the packet to be processed.

    Returns
    -------
    combined : xr.Dataset
        Combined dataset.
    """
    s3 = boto3.client("s3")
    xtce_ialirt_path = (
        imap_module_directory / "ialirt" / "packet_definitions" / "ialirt.xml"
    )
    datasets = []

    for filename in filenames:
        local_path = download_dir / Path(filename).name
        s3.download_file(bucket, filename, str(local_path))
        xarray_data = packet_file_to_datasets(local_path, xtce_ialirt_path)[apid]
        datasets.append(xarray_data)

    combined = xr.concat(datasets, dim="epoch")
    # Drop duplicate epochs. This could happen if there are duplicate packets.
    _, unique_idx = np.unique(combined["epoch"], return_index=True)
    combined = combined.isel(epoch=sorted(unique_idx))

    return combined


def process_algorithms(combined: xr.Dataset, algorithm_table):
    """Process the algorithms and insert data, as needed.

    Parameters
    ----------
    combined : xr.Dataset
        L0 parsed data.
    algorithm_table : dynamodb.Table
        The DynamoDB table to insert or update the data.
    """
    processors = [
        ("mag", process_packet),
        ("hit", process_hit),
        ("swe", process_swe),
        ("codicelo", process_codice),
        ("codicehi", process_codice),
        ("swapi", process_swapi_ialirt),
    ]

    for instrument, process_func in processors:
        if instrument == "swe":
            logger.info("Processing SWE.")
            download_path = get_ancillary(instrument, "l1b-in-flight-cal")
            logger.info("swe l1b-in-flight-cal: %s", download_path)
            result = process_func(combined, [download_path])
        elif instrument == "mag":
            logger.info("Processing MAG.")
            download_path = get_ancillary(instrument, "ialirt-calibration")
            parts = download_path.stem.split("_")
            date_str = parts[-2]
            input_files = AncillaryInput(download_path.name)
            ialirt_calibration_data = MagAncillaryCombiner(input_files, date_str)
            logger.info("mag ialirt-calibration: %s", download_path)

            download_path = get_ancillary(instrument, "l1b-calibration")
            parts = download_path.stem.split("_")
            date_str = parts[-2]
            input_files = AncillaryInput(download_path.name)
            l1b_calibration_data = MagAncillaryCombiner(input_files, date_str)
            logger.info("mag l1b-calibration: %s", download_path)

            result = process_packet(
                combined, l1b_calibration_data.combined_dataset, ialirt_calibration_data.combined_dataset
            )
        elif instrument == "codicelo":
            logger.info("Processing CoDICE-Lo.")
            result, _ = process_func(combined)
        elif instrument == "codicehi":
            logger.info("Processing CoDICE-Hi.")
            _, result = process_func(combined)
        elif instrument == "swapi":
            logger.info("Processing SWAPI.")
            download_path = get_ancillary(instrument, "esa-unit-conversion")
            logger.info("swapi esa-unit-conversion: %s", download_path)
            calibration_data = pd.read_csv(download_path)
            result = process_func(combined, calibration_data)
        else:
            logger.info("Processing HIT.")
            result = process_func(combined)

        logger.info("%s result: %s", instrument, result)

        if any(result) and all(result):
            insert_data(result, algorithm_table, instrument)


def insert_data(data: list[dict], algorithm_table, instrument: str):
    """Insert or update database row, depending on content of item.

    Parameters
    ----------
    data : list[dict]
        Data product produced from processing respectively instrument.
    algorithm_table : dynamodb.Table
        The DynamoDB table to insert or update the data.
    instrument : str
        The prefix for the product name.
    """
    apid = data[0]["apid"]
    mets = [item["met"] for item in data]
    min_met = min(mets)
    max_met = max(mets)
    logger.info(f"Processing mets {min_met} to {max_met}.")
    logger.info(f"Processing utc {met_to_utc(min_met)} to {met_to_utc(max_met)}.")

    # Query existing items.
    response = algorithm_table.query(
        KeyConditionExpression=Key("apid").eq(apid)
        & Key("met").between(min_met, max_met)
    )

    existing_items = {item["met"]: item for item in response.get("Items", [])}

    # Insert or update as needed
    for raw in data:
        met = raw["met"]
        key = {"apid": apid, "met": met}
        existing = existing_items.get(met)
        raw["last_modified"] = datetime.now(timezone.utc).isoformat()

        # Calculate the spacecraft position and velocity in GSM coordinates.
        et = sct_to_et(met_to_sclkticks(met))
        gsm_state = imap_state(
            [et], ref_frame=SpiceFrame.IMAP_GSM, observer=SpiceBody.EARTH
        )
        gse_state = imap_state(
            [et], ref_frame=SpiceFrame.IMAP_GSE, observer=SpiceBody.EARTH
        )
        print(gsm_state)

        raw["sc_position_GSM"] = [Decimal(str(val)) for val in gsm_state[0, :3]]
        raw["sc_velocity_GSM"] = [Decimal(str(val)) for val in gsm_state[0, 3:]]
        raw["sc_position_GSE"] = [Decimal(str(val)) for val in gse_state[0, :3]]
        raw["sc_velocity_GSE"] = [Decimal(str(val)) for val in gse_state[0, 3:]]

        if existing:
            if any(key.startswith(instrument) for key in existing.keys()):
                continue

            update_expr = "SET " + ", ".join(
                f"{field} = :{field}"
                for field in raw
                if field
                not in {
                    "apid",
                    "met",
                    "met_in_utc",
                    "ttj2000ns",
                    "sc_position_GSM",
                    "sc_velocity_GSM",
                    "sc_position_GSE",
                    "sc_velocity_GSE",
                }
            )

            expression_values = {
                f":{field}": value
                for field, value in raw.items()
                if field
                not in {
                    "apid",
                    "met",
                    "met_in_utc",
                    "ttj2000ns",
                    "sc_position_GSM",
                    "sc_velocity_GSM",
                    "sc_position_GSE",
                    "sc_velocity_GSE",
                }
            }

            algorithm_table.update_item(
                Key=key,
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expression_values,
            )
        else:
            algorithm_table.put_item(Item=raw)
        logger.info(f"Inserted {instrument.upper()}.")


def lambda_handler(event, context):
    """Create metadata and add it to the database.

    This function is an event handler for s3 ingest bucket.
    It is also used to ingest data to the DynamoDB table.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    """
    logger.info("Received event: %s", json.dumps(event))

    algorithm_table_name = os.environ.get("ALGORITHM_TABLE")
    dynamodb = boto3.resource("dynamodb")
    algorithm_table = dynamodb.Table(algorithm_table_name)
    url = os.environ.get("IMAP_DATA_ACCESS_URL")

    bucket = event["detail"]["bucket"]["name"]
    region = event["region"]

    s3_filepath = event["detail"]["object"]["key"]
    filename = os.path.basename(s3_filepath)
    logger.info("Retrieved filename: %s", filename)
    dependency_inputs = get_latest_spice_kernels(url)
    logger.info("dependency_inputs: %s", dependency_inputs)
    download_spice_file(dependency_inputs)

    # Query s3 for packet filenames from past 5 minutes.
    if "now" in event:
        now = datetime.fromisoformat(event["now"].replace("Z", "")).replace(
            tzinfo=timezone.utc
        )
    else:
        now = datetime.now(timezone.utc)
    bucket = "ialirt-301233867300"
    filenames = query_filenames("ialirt-301233867300", region, now)

    if filenames:
        logger.info("Found %d files to process", len(filenames))
        logger.info("Parsing packets.")
        # Get packets into datasets and combine.
        combined = parse_packets(filenames, bucket, Path("/tmp"))  # noqa: S108
        logger.info("Packets parsed. Processing algorithms.")
        # Process algorithms and insert new data.
        process_algorithms(combined, algorithm_table)

        logger.info("Successfully wrote all new items to DynamoDB")
    else:
        logger.info("No files found to process in the last 5 minutes.")
