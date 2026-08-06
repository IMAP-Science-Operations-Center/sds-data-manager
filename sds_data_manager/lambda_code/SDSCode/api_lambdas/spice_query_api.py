"""Contains the lambda handler for the 'query' data access API."""

import datetime
import json
import logging

import spiceypy
from imap_data_access import SPICEFilePath
from sqlalchemy import func, select

from ..database import database as db
from ..database import models
from ..spice_utilities import furnish_best_spice_file
from . import non_spice_table_api

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# Map the 'type' param in this API to the rawPath that `non_spice_table_api` expects.
_NON_SPICE_RAW_PATHS = {
    "spin": "/spin-table",
    "repoint": "/repoint-table",
    "thruster": "/small-forces-table",
}


def lambda_handler(event, context):
    """Entry point to the SPICE query API lambda.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    Notes
    -----
    The optional ``type`` query parameter controls which table is queried:

    * ``spin`` - spin files table
    * ``repoint`` - repoint files table
    * ``thruster`` - small-forces (thruster) files table
    * ``kernels`` - All supported SPICE kernels types
    * ``<other>`` - A specific SPICE kernel type from the SPICE files table

    Passing a kernel-type value such as ``attitude_history`` filters the SPICE table
    by ``kernel_type``. The remaining SPICE-specific parameters
    (``file_name``, ``start_time``, ``end_time``, ``latest``,
    ``start_ingest_date``, ``end_ingest_date``) apply.

    When ``type`` is one of the non-SPICE values, the ``file_name`` parameter is
    renamed to ``file_path``, ``start_time`` is mapped to ``start_date``, ``end_time``
    is mapped to ``end_date``, and the remaining parameters are passed to the
    non-SPICE API.
    """
    logger.debug("SPICE Query Event: " + json.dumps(event, indent=2))

    # Initialize status_code to 400
    status_code = 400

    query_params = event.get("queryStringParameters", {})
    table_type = query_params.get("type", "kernels")

    if table_type in _NON_SPICE_RAW_PATHS:
        # Remove `type`, since it is not a valid non-SPICE query parameter.
        query_params.pop("type")

        # Remap incoming parameters to ones supported by the non-SPICE tables API.
        try:
            query_params = _remap_to_non_spice_params(query_params)
        except ValueError:
            err_msg = "Expected start/end times in ET."
            return non_spice_table_api.get_json_response(status_code, err_msg)

        return non_spice_table_api.lambda_handler(
            {
                "rawPath": _NON_SPICE_RAW_PATHS[table_type],
                "queryStringParameters": query_params,
            },
            context,
        )

    # add session, pick model like in indexer and add query to filter_as
    query_params = event["queryStringParameters"]
    with db.Session() as session:
        # select the SPICE files table for the query
        query = select(models.SPICEFiles)

        # get a list of all valid search parameters
        valid_parameters = [
            "file_name",
            "start_time",
            "end_time",
            "type",
            "latest",
            "start_ingest_date",
            "end_ingest_date",
        ]

        # go through each query parameter to set up sqlalchemy query conditions
        for param, value in query_params.items():
            # confirm that the query parameter is valid
            if param not in valid_parameters:
                err_msg = (
                    f"{param} is not a valid query parameter. "
                    f"Valid query parameters are: {valid_parameters}"
                )
                response = non_spice_table_api.get_json_response(status_code, err_msg)
                logger.debug(err_msg)
                return response
            try:
                if param == "start_time":
                    query = query.where(
                        models.SPICEFiles.max_date_j2000 >= float(value)
                    )
                elif param == "end_time":
                    query = query.where(
                        models.SPICEFiles.min_date_j2000 <= float(value)
                    )
                elif param == "type" and value != "kernels":
                    query = query.where(models.SPICEFiles.kernel_type == value)
                elif param == "file_name":
                    query = query.where(models.SPICEFiles.file_name == value)
                elif param == "latest" and value.lower() == "true":
                    # Make a subquery that gives us (file_root, MAX(version))
                    latest_versions_subq = (
                        session.query(
                            models.SPICEFiles.file_root,
                            func.max(models.SPICEFiles.version).label("max_version"),
                        )
                        .group_by(models.SPICEFiles.file_root)
                        .subquery()
                    )

                    # Join main query to subquery so that we only keep rows
                    # with the matching max version for each file_root
                    query = query.join(
                        latest_versions_subq,
                        (
                            models.SPICEFiles.file_root
                            == latest_versions_subq.c.file_root
                        )
                        & (
                            models.SPICEFiles.version
                            == latest_versions_subq.c.max_version
                        ),
                    )
                elif param == "start_ingest_date":
                    parsed_date = datetime.datetime.strptime(value, "%Y%m%d")
                    query = query.where(models.SPICEFiles.ingestion_date >= parsed_date)
                elif param == "end_ingest_date":
                    parsed_date = datetime.datetime.strptime(value, "%Y%m%d")
                    query = query.where(models.SPICEFiles.ingestion_date <= parsed_date)
            except ValueError:
                err_msg = f"Invalid value for {param}: {value}"
                response = non_spice_table_api.get_json_response(status_code, err_msg)
                logger.debug(err_msg)
                return response

        # If we got this far, reset status_code to 200
        status_code = 200

        search_results = session.execute(query).scalars().all()

        search_results = [
            _convert_spice_metadata_model_to_dict(result) for result in search_results
        ]
        logger.debug(
            "Found [%s] Query Search Results: %s",
            len(search_results),
            str(search_results),
        )

        return non_spice_table_api.get_json_response(status_code, search_results)


def _remap_to_non_spice_params(query_params: dict) -> dict:
    """Remap SPICE query parameters to those supported by the non-SPICE tables API."""
    if "file_name" in query_params:
        query_params["file_path"] = query_params.pop("file_name")

    if "start_time" in query_params or "end_time" in query_params:
        try:
            spiceypy.et2datetime(0)
        except spiceypy.utils.exceptions.SpiceMISSINGTIMEINFO:
            furnish_best_spice_file("leapseconds")

        try:
            if "start_time" in query_params:
                query_params["start_date"] = spiceypy.et2datetime(
                    float(query_params.pop("start_time"))
                ).strftime("%Y%m%d")
            if "end_time" in query_params:
                query_params["end_date"] = spiceypy.et2datetime(
                    float(query_params.pop("end_time"))
                ).strftime("%Y%m%d")
        except spiceypy.utils.exceptions.SpiceError as e:
            raise ValueError(f"Invalid ET value for start_time/end_time: {e}") from e

    return query_params


def _convert_spice_metadata_model_to_dict(file: models.SPICEFiles) -> dict:
    """Convert a sqlalchemy query to SPICEFiles to a dictionary.

    Paramters
    ----------
    file: models.SPICEFiles
        A single row from the SPICEFiles table

    Returns
    -------
    spice_file_dict: dict
        The SPICE file query as a dictionary
    """
    spice_file_dict = {
        "file_name": (
            SPICEFilePath(file.file_name).construct_path().parent.name
            + "/"
            + file.file_name
        ),
        "file_root": file.file_root,
        "kernel_type": file.kernel_type,
        "version": file.version,
        "min_date_j2000": file.min_date_j2000,
        "max_date_j2000": file.max_date_j2000,
        "file_intervals_j2000": file.file_intervals_j2000,
        "min_date_datetime": file.min_date_datetime.strftime("%Y-%m-%d, %H:%M:%S"),
        "max_date_datetime": file.max_date_datetime.strftime("%Y-%m-%d, %H:%M:%S"),
        "file_intervals_datetime": file.file_intervals_datetime,
        "min_date_sclk": file.min_date_sclk,
        "max_date_sclk": file.max_date_sclk,
        "file_intervals_sclk": file.file_intervals_sclk,
        "sclk_kernel": file.sclk_kernel,
        "lsk_kernel": file.lsk_kernel,
        "ingestion_date": file.ingestion_date.strftime("%Y-%m-%d, %H:%M:%S"),
        "timestamp": file.ingestion_date.replace(
            tzinfo=datetime.timezone.utc
        ).timestamp(),
    }
    return spice_file_dict
