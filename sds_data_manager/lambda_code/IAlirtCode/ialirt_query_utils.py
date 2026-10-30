"""Shared helpers for the I-ALiRT query lambdas."""

import json
from datetime import datetime
from decimal import Decimal

FULL_SCOPES = {
    "read",
    "full",
}

# Read-only scopes (can read but not write)
READ_ONLY_SCOPES = {
    "read",
}

RESTRICTED_FIELDS = {
    "hit_e_a_side_high_en",
    "hit_e_b_side_high_en",
    "hit_h_a_side_high_en",
    "hit_h_b_side_high_en",
    "hit_e_a_side_low_en",
    "hit_e_b_side_low_en",
}

PUBLIC_CUTOFF_UTC = "2026-02-01T00:00:00"


class BadTimeError(Exception):
    """Raised when the time filters are invalid."""

    pass


class DecimalEncoder(json.JSONEncoder):
    """Convert Decimals to floats."""

    def default(self, obj):
        """Override JSON encoding for Decimal values."""
        if isinstance(obj, Decimal):
            # - If the Decimal is an integer, return int
            # - Otherwise, float rounded to 3 decimal places
            if obj == obj.to_integral_value():
                return int(obj)
            return round(float(obj), 3)

        # Let the base class raise for other unsupported types
        return super().default(obj)


def validate_time(ts: str):
    """Validate a timestamp string in ISO 8601 format (YYYY-MM-DDTHH:MM:SS).

    Parameters
    ----------
    ts : str
        The timestamp string to validate.

    Returns
    -------
    datetime
        A `datetime` object if the timestamp is valid.

    Raises
    ------
    BadTimeError
        If the format is invalid.
    """
    try:
        return datetime.fromisoformat(ts)
    except Exception as e:
        raise BadTimeError("Invalid time format") from e


def _error(code: int, message: str) -> dict:
    """Create error dictionary.

    Parameters
    ----------
    code : int
        Error code.
    message : str
        The error message.

    Returns
    -------
    error : dict
        The error dictionary.
    """
    return {
        "statusCode": code,
        "body": json.dumps({"message": message}),
        "headers": {"Content-Type": "application/json"},
    }


def filter_items_by_scope(items: list[dict], scope: str) -> list[dict]:
    """Hide selected HIT fields for scopes that are not full HIT.

    Parameters
    ----------
    items : list[dict]
        Items returned from DynamoDB.
    scope : str
        Scope string from the API key authorizer.

    Returns
    -------
    filtered_items: list[dict]
        Items list.
    """
    # If caller has full HIT access, do nothing
    if scope in FULL_SCOPES or scope in READ_ONLY_SCOPES:
        return items

    filtered_items = [
        {k: v for k, v in item.items() if k not in RESTRICTED_FIELDS} for item in items
    ]

    return filtered_items
