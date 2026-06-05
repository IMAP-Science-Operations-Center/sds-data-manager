import json
import logging
import datetime
from dagster import (
    asset,
    sensor, 
    SensorEvaluationContext, 
    SensorResult, 
    AssetSelection,
    DefaultSensorStatus,
    Failure
)
import imap_data_access
from sds_data_manager.lambda_code.SDSCode.database import database as db, models
from sds_data_manager.lambda_code.SDSCode.api_lambdas import spice_metakernel_api
from sds_data_manager.orchestration import dagster_utilities, config
from sds_data_manager.orchestration.types import DependencyNode

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def check_requested_kernels(combined_kernel_sources, metakernel_files):
    """Check if all requested kernels are present in the metakernel files.

    We need to ensure that the returned list of metakernel files includes
    all requested kernels, especially for ephemeris kernels. The API can
    return the "best" ephemeris kernels, which can include both historical
    and predicted kernels depending on the input time range. If the user
    specifically requests only historical ephemeris kernels, we must verify
    that only historical files are returned. Otherwise, both historical
    and predicted kernels are acceptable.

    Additionally, the API can return multiple kernels for the same source
    if the files cover specific date ranges. Because of this, we must
    check that all requested sources are present in the returned
    metakernel files, rather than performing a direct one-to-one
    comparison. Each source may correspond to multiple kernel files.

    Parameters
    ----------
    combined_kernel_sources : str
        Comma-separated string of requested kernel sources.
    metakernel_files : list
        List of metakernel files found.

    Returns
    -------
    bool
        True if all requested kernels are found, False otherwise.
    """
    requested_kernels = set(combined_kernel_sources.split(","))
    expected_ephemeris = set(
        [kernel for kernel in requested_kernels if "ephemeris_" in kernel]
    )
    expected_other_kernels = set(
        [kernel for kernel in requested_kernels if "ephemeris_" not in kernel]
    )

    ephemeris_found = set()
    other_kernels_found = set()

    for file in metakernel_files:
        file_obj = imap_data_access.SPICEFilePath(file)
        # Extract the kernel type from the file name
        kernel_type = file_obj.spice_metadata["type"]
        if "ephemeris_" in kernel_type:
            ephemeris_found.add(kernel_type)
        else:
            other_kernels_found.add(kernel_type)

    # Check if all other requested kernels are found
    if expected_other_kernels != other_kernels_found:
        logger.error(
            f"Non-ephemeris kernels {expected_other_kernels} not found in "
            f"metakernel files {other_kernels_found}"
        )
        return False

    # If no ephemeris kernels are requested, we can return True.
    if not expected_ephemeris:
        return True

    # If only historical ephemeris kernel is requested, check that it
    # is found.
    if (
        len(expected_ephemeris) == 1
        and next(iter(expected_ephemeris)) == "ephemeris_reconstructed"
        and "ephemeris_reconstructed" in ephemeris_found
    ):
        return True

    # If 'best' ephemeris kernel is requested, check that at least one of the kernels
    # is found in the metakernel files.
    if (
        len(expected_ephemeris) > 1
        and any("ephemeris_" in kernel for kernel in expected_ephemeris)
        and any("ephemeris_" in kernel for kernel in ephemeris_found)
    ):
        return True

    logger.error(
        f"Requested ephemeris kernels: {expected_ephemeris}, "
        f"found in metakernel files: {ephemeris_found}"
        f"\nRequested other kernels: {expected_other_kernels}, "
        f"found in metakernel files: {other_kernels_found}"
    )
    return False


# ruff: noqa: PLR0915, PLR0912, PLR0911
def get_upstream_dependency_inputs_spice(
    dependencies: list,
    start_date: datetime.datetime,
    end_date: datetime.datetime,
    repoint: int | list[int] = None,
):
    """Construct a ProcessingInputCollection of dependency files.

    For each dependency, query for existing files in s3 and add any matching files
    found to a ProcessingInputCollection.

    Parameters
    ----------
    dependencies : list
        List of dependency dictionaries either downstream or upstream from the
        dependency in the query parameters.
    start_date : datetime
        Start date to find dependent files with.
    end_date : datetime
        End date to find dependent files with.
    repoint : int or list[int], optional
        If provided, will be used to filter files by repoint number(s). Can be a
        single int or a list of ints.
    open_session : db.Session, optional
        Database session. If not provided, a new session will be created.

    Returns
    -------
    ProcessingInputCollection
        Dependency files that can include Ancillary, SPICE, or Science inputs.
    """

    # convert start_date and end_date in seconds after j2000.
    # TODO: remove this once Bryan changes takes in 'yyyymmdd' format
    def yyyymmdd_to_seconds_since_j2000(
        date_str: str, add_24_hrs=False
    ) -> float:
        # Parse input date string
        dt = datetime.datetime.strptime(date_str, "%Y%m%d").replace(
            tzinfo=datetime.timezone.utc
        )
        if add_24_hrs:
            dt += datetime.timedelta(hours=24)
        # Define J2000 epoch: 2000-01-01T12:00:00 UTC
        j2000 = datetime.datetime(2000, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)

        # Compute seconds difference
        delta = dt - j2000
        return delta.total_seconds()

    start_time = yyyymmdd_to_seconds_since_j2000(
        start_date.strftime("%Y%m%d")
    )
    # TODO revisit setting end_time after SIT-4. Should be handled upstream
    if end_date == start_date or repoint is not None:
        add_24_hrs = True
    else:
        add_24_hrs = False
    end_time = yyyymmdd_to_seconds_since_j2000(
        end_date.strftime("%Y%m%d"), add_24_hrs
    )
    metakernel_response = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": start_time,
                "end_time": end_time,
                "list_files": "True",
                "file_types": ",".join(dependencies),
                # TODO: revisit this after SIT-4
                # "require_coverage": "True",
            }
        },
        None,
    )
    if metakernel_response["statusCode"] != 200:
        logger.error(
            f"Metakernel lambda raised error: {metakernel_response['body']}"
        )
        return None
    metakernel_files = json.loads(metakernel_response["body"])
    # If number of kernels returned doesn't match the number of file types
    # requested
    has_all_kernels = check_requested_kernels(
        ",".join(dependencies), metakernel_files
    )
    if not has_all_kernels:
        return None

    logger.info(
        f"Found metakernel files: {metakernel_files}. Adding to collection."
    )
    return metakernel_files