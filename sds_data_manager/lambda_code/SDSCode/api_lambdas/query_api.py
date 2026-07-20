"""Contains the lambda handler for the 'query' data access API."""

import datetime
import json
import logging
from enum import StrEnum

from sqlalchemy import func, select

from ..api_lambdas.utils import is_authenticated_user
from ..api_lambdas.latest_version_query import build_latest_version_query
from ..database import database as db
from ..database import models

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Maps the `table` query param to its model.
_TABLE_MODELS = {
    "science": models.ScienceFiles,
    "ancillary": models.AncillaryFiles,
    "spice": models.SPICEFiles,
    "quicklook": models.QuicklookFiles,
}

# Valid query parameters include...
#   all table columns
# + ingestion_start_date/ingestion_end_date,
# + "end_date" for tables with a start_date but no end_date
#   (science/quicklook have start_date only; ancillary has both, spice has neither).
_VALID_PARAMETERS = {
    table: [
        *model.__table__.c.keys(),
        *(
            ["end_date"]
            if "start_date" in model.__table__.c
            and "end_date" not in model.__table__.c
            else []
        ),
        "ingestion_start_date",
        "ingestion_end_date",
    ]
    for table, model in _TABLE_MODELS.items()
}


class LatestVersionMode(StrEnum):
    """The two ways a science query resolves "latest"."""

    # single newest file per series (latest major, then latest minor)
    NEWEST = "newest"
    # every minor version of the latest major
    LATEST_MAJOR = "latest_major"


def _parse_version_alias(value):
    """Translate a legacy ``version`` string into ``(major, minor)`` integers.

    The ``version`` query parameter is kept for backwards compatibility after
    the science ``version`` column was split into ``major_version`` and
    ``minor_version``. Accepts the full ``vMMM.mmmm`` form or the deprecated
    minor-only ``vXXX`` form.

    Parameters
    ----------
    value : str
        The ``version`` query parameter value.

    Returns
    -------
    tuple
        ``(major, minor)`` where ``major`` is ``None`` for the minor-only form.

    """
    digits = value.lstrip("vV")
    if "." in digits:
        major_str, minor_str = digits.split(".", 1)
        return int(major_str), int(minor_str)
    return None, int(digits)


def _resolve_science_version_mode(query_params):
    """Resolve science version params in place and return the latest mode.

    Applies the backwards-compatible ``version`` alias and reads the ``latest``
    flag, mutating ``query_params`` so the generic query loop only sees real
    columns.

    Parameters
    ----------
    query_params : dict
        The (mutable) query parameters; updated in place.

    Returns
    -------
    LatestVersionMode or None
        A ``LatestVersionMode`` value, or None when a concrete major_version
        was requested (no latest restriction applied).

    Raises
    ------
    ValueError
        If the ``version`` alias value cannot be parsed.

    """
    # Backwards-compatible `version` alias -> minor_version (and major_version
    # when the full vMMM.mmmm form is provided).
    if "version" in query_params:
        major, minor = _parse_version_alias(query_params.pop("version"))
        if major is not None:
            query_params["major_version"] = major
        query_params["minor_version"] = minor

    # `latest=true` -> the single newest file (latest major + latest minor).
    latest_flag = str(query_params.pop("latest", "")).lower() == "true"

    # Precedence: a concrete major_version wins (None -> no latest restriction),
    # then latest=true, then the default (omitting major_version -> latest
    # major, all minor versions).
    if "major_version" in query_params:
        return None
    if latest_flag:
        return LatestVersionMode.NEWEST
    return LatestVersionMode.LATEST_MAJOR


def _filter_condition(cols, param, value):
    """Build the SQLAlchemy filter expression for one query parameter.

    Parameters
    ----------
    cols
        The column collection to filter against.
    param : str
        The query parameter name.
    value : str
        The query parameter value.

    Returns
    -------
    ColumnElement
        A SQLAlchemy boolean clause to AND into the query.

    """
    match param:
        case "start_date":
            return cols.start_date >= datetime.datetime.strptime(value, "%Y%m%d")
        case "end_date":
            # TODO: Need to discuss as a team how to handle date queries. For now,
            # the date queries will only look at the file start_date.
            return cols.start_date <= datetime.datetime.strptime(value, "%Y%m%d")
        case "ingestion_start_date":
            return func.date(cols.ingestion_date) >= (
                datetime.datetime.strptime(value, "%Y%m%d").date()
            )
        case "ingestion_end_date":
            return func.date(cols.ingestion_date) <= (
                datetime.datetime.strptime(value, "%Y%m%d").date()
            )
        case _:
            return cols[param] == value


def _format_search_results(search_results):
    """Stringify datetime fields in query results for the JSON response.

    Parameters
    ----------
    search_results : list
        List of result dicts; mutated in place.

    Returns
    -------
    list
        The same list with date fields formatted as strings.

    """
    for result in search_results:
        if "major_version" in result and "minor_version" in result:
            result["version"] = (
                f"v{result['major_version']:03d}.{result['minor_version']:04d}"
            )
        result["start_date"] = result["start_date"].strftime("%Y%m%d")
        if result.get("end_date"):
            result["end_date"] = result["end_date"].strftime("%Y%m%d")
        ingestion = result["ingestion_date"]
        if ingestion.tzinfo is not None:
            # Convert to UTC and drop the timezone.
            ingestion = ingestion.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        result["ingestion_date"] = ingestion.strftime("%Y%m%d %H:%M:%S")
    return search_results


def lambda_handler(event, context):
    """Entry point to the query API lambda.

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
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    logger.info("Received event: " + json.dumps(event, indent=2))

    # Make a mutable copy so we can pre-process science version parameters.
    query_params = dict(event["queryStringParameters"])
    query_table = query_params.pop("table", "science")
    logger.info(f"Querying table: {query_table}")

    model = _TABLE_MODELS[query_table]
    table_columns = model.__table__.c

    version_mode = None
    if query_table == "science":
        try:
            version_mode = _resolve_science_version_mode(query_params)
        except ValueError:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    "Invalid 'version' value. Use format 'vMMM.mmmm' or 'vXXX'."
                ),
            }

    valid_parameters = _VALID_PARAMETERS[query_table]
    filters = []
    for param, value in query_params.items():
        if param not in valid_parameters:
            response = {
                "statusCode": 400,
                "body": json.dumps(
                    f"{param} is not a valid query parameter for {query_table} table. "
                    + f"Valid query parameters are: {valid_parameters}"
                ),
            }
            logger.debug(
                f"Received an invalid query parameter [{param}] for table "
                "{query_table}, valid options are: {valid_parameters}"
            )
            return response
        filters.append(_filter_condition(table_columns, param, value))

    # if not authenticated, restrict to released only
    authenticated = is_authenticated_user(event)
    if not authenticated:
        filters.append(table_columns.released)

    if version_mode is None:
        # if not filtering by version, simply SELECT ... WHERE ...
        query = select(model.__table__).where(*filters)
        cols = table_columns
    else:
        # otherwise, also include the rank subquery
        query = build_latest_version_query(
            filters=filters,
            major_only=version_mode == LatestVersionMode.LATEST_MAJOR,
        )
        # important not to use table_columns hereafter
        cols = query.selected_columns

    # We want to order the query returns by the filename
    # This will implicitly sort by: instrument, data level, descriptor, start_date, ...
    # Default for the table is by the ascending id so by insertion order
    # This fails for the SPICE table because it uses 'file_name'
    query = query.order_by(cols.file_path)

    with db.Session() as session:
        search_results = session.execute(query).all()

        # Convert the search results (list of tuples) to a list of dicts and
        # stringify their datetime fields for the JSON response.
        search_results = [result._asdict() for result in search_results]
        search_results = _format_search_results(search_results)

        logger.info(
            "Found [%s] Query Search Results: %s",
            len(search_results),
            str(search_results),
        )

        # Format the response
        response = {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(search_results),  # returns a list of tuples
        }

    return response
