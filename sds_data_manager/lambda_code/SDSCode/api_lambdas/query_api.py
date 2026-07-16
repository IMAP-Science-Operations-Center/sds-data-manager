"""Contains the lambda handler for the 'query' data access API."""

import datetime
import json
import logging
from collections import namedtuple

from sqlalchemy import and_, func, or_, select

from ..api_lambdas.utils import is_authenticated_user
from ..database import database as db
from ..database import models

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Columns that, together with repointing, identify a unique science file
# "series" when resolving the latest version.
_VERSION_GROUPING_COLUMNS = (
    "instrument",
    "data_level",
    "descriptor",
    "start_date",
)

# The two ways a science query resolves "latest". NEWEST keeps only the single
# newest file per series (latest major, then latest minor); LATEST_MAJOR keeps
# every minor version of the latest major.
LatestVersionMode = namedtuple("LatestVersionMode", ["newest", "latest_major"])
LATEST_VERSION_MODE = LatestVersionMode(newest="newest", latest_major="latest_major")


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
    str or None
        A ``LATEST_VERSION_MODE`` value, or None when a concrete major_version
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
        return LATEST_VERSION_MODE.newest
    return LATEST_VERSION_MODE.latest_major


def _latest_version_filters(model, mode, released_only):
    """Build WHERE clauses restricting science results to the latest version.

    "Latest" is resolved with correlated subqueries: for each candidate result
    row, a subquery computes the maximum version within that row's file
    "series" (same instrument / data_level / descriptor / start_date /
    repointing) and the row is kept only if it matches that maximum.

    Parameters
    ----------
    model
        The science table model.
    mode : str
        One of ``LATEST_VERSION_MODE``. ``latest_major`` keeps the latest major
        version with ALL of its minor versions; ``newest`` keeps only the single
        newest file (latest major and, within it, latest minor).
    released_only : bool
        When True, only released files count toward "latest". This is applied
        *inside* the max subqueries (not as a plain filter on the outer query)
        so that an unreleased newer version cannot hide the latest released
        version from unauthenticated users.

    Returns
    -------
    list
        SQLAlchemy boolean clauses to AND into the query.

    """
    outer = model.__table__

    def same_series_as_outer(inner):
        """Conditions correlating an inner alias to the outer row's series."""
        # Equality on every grouping column that defines one file series.
        conditions = [
            getattr(inner.c, column) == getattr(outer.c, column)
            for column in _VERSION_GROUPING_COLUMNS
        ]
        # repointing is nullable, so NULL-safe equality keeps NULL-repointing
        # rows grouped together instead of dropping them from the correlation.
        conditions.append(
            or_(
                inner.c.repointing == outer.c.repointing,
                and_(inner.c.repointing.is_(None), outer.c.repointing.is_(None)),
            )
        )
        # Only released files participate in the max when released_only is set.
        if released_only:
            conditions.append(inner.c.released.is_(True))
        return conditions

    # Keep rows whose major_version is the max major within their series.
    # Each max subquery re-scans the science table, so it needs its own aliased
    # reference to that table (a self-join): the alias is the inner scan, while
    # the un-aliased `outer` is the row being tested, which the subquery
    # correlates back to via same_series_as_outer().
    major_inner = outer.alias("latest_major_inner")
    max_major = (
        select(func.max(major_inner.c.major_version))
        .where(*same_series_as_outer(major_inner))
        .scalar_subquery()
    )
    filters = [outer.c.major_version == max_major]

    # For "newest", additionally keep only the max minor within that major.
    if mode == LATEST_VERSION_MODE.newest:
        # A second, independent self-join alias for the minor-version scan.
        minor_inner = outer.alias("latest_minor_inner")
        max_minor = (
            select(func.max(minor_inner.c.minor_version))
            .where(
                *same_series_as_outer(minor_inner),
                minor_inner.c.major_version == outer.c.major_version,
            )
            .scalar_subquery()
        )
        filters.append(outer.c.minor_version == max_minor)
    return filters


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


def lambda_handler(event, context):  # noqa: PLR0912
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

    TableModels = namedtuple(
        "TableModels", ["science", "ancillary", "spice", "quicklook"]
    )

    table_models = TableModels(
        science=models.ScienceFiles,
        ancillary=models.AncillaryFiles,
        spice=models.SPICEFiles,
        quicklook=models.QuicklookFiles,
    )

    # add session, pick model like in indexer and add query to filter_as
    # Make a mutable copy so we can pre-process science version parameters.
    query_params = dict(event["queryStringParameters"])
    # get desired table for query
    query_table = query_params.get("table", "science")

    logger.info(f"Querying table: {query_table}")
    model = getattr(table_models, query_table)

    # select the given table for the query
    authenticated = is_authenticated_user(event)
    query = select(model.__table__)
    if not authenticated:
        query = query.filter(model.released)

    # Science-only version handling: a backwards-compatible `version` alias plus
    # server-side resolution of "latest". Other tables keep `version` as a real
    # column and are left untouched.
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

    # get a list of all valid search parameters
    valid_parameters = [
        column.key for column in model.__table__.columns if column.key not in ["id"]
    ]
    # Up until this point, valid_parameters are the same as the
    # columns in the selected table. And looks like we removed
    # the "id" column from the list. But we also need to add
    # 'end_date' to the list of valid_parameters but only for
    # the science table.
    if query_table != "ancillary":
        valid_parameters.append("end_date")
    valid_parameters.append("ingestion_start_date")
    valid_parameters.append("ingestion_end_date")

    # go through each query parameter to set up sqlalchemy query conditions
    for param, value in query_params.items():
        # skip the table parameter
        if param == "table":
            continue
        # confirm that the query parameter is valid
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
        # check if we're search for start_date or end date or ingestion dates to
        # setup the correct "where" time condition
        if param == "start_date":
            query = query.where(
                model.start_date >= datetime.datetime.strptime(value, "%Y%m%d")
            )
        elif param == "end_date":
            # TODO: Need to discuss as a team how to handle date queries. For now,
            # the date queries will only look at the file start_date.
            query = query.where(
                model.start_date <= datetime.datetime.strptime(value, "%Y%m%d")
            )
        elif param == "ingestion_start_date":
            # filtering by ingestion date
            query = query.where(
                func.date(model.ingestion_date)
                >= datetime.datetime.strptime(value, "%Y%m%d").date()
            )
        elif param == "ingestion_end_date":
            query = query.where(
                func.date(model.ingestion_date)
                <= datetime.datetime.strptime(value, "%Y%m%d").date()
            )
        # all non-time string matching parameters
        else:
            query = query.where(getattr(model, param) == value)

    # Restrict science results to the latest version when no concrete
    # major_version was requested. NEWEST keeps a single file per series;
    # LATEST_MAJOR keeps all minor versions of the latest major.
    if version_mode is not None:
        for clause in _latest_version_filters(model, version_mode, not authenticated):
            query = query.where(clause)

    # We want to order the query returns by the filename
    # This will implicitly sort by: instrument, data level, descriptor, start_date, ...
    # Default for the table is by the ascending id so by insertion order
    # This fails for the SPICE table because it uses 'file_name'
    query = query.order_by(model.file_path)

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
