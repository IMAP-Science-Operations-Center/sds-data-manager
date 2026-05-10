"""Common functions for pipeline lambdas."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, ClassVar

from .. import VALID_CADENCE_STRS
from ..dependency import DataSource, DataType

# Date range validation constants
NEAREST_OPTIONS = ("nd", "np")
DATE_RANGE_OPTIONS = ("p", "h", "d", "l", *NEAREST_OPTIONS)


@dataclass
class DependencyNode:
    """Shared between batch starter and dependency lambda."""

    _data_source_validator: ClassVar[DataSource] = DataSource()
    _data_type_validator: ClassVar[DataType] = DataType()

    source: str
    data_type: str
    descriptor: str
    required: bool = True
    kickoff_job: bool = True
    date_range: list = None

    def __post_init__(self):
        """Validate all fields on construction.

        A valid node must have exactly 5 or 6 elements:
            [
                source,
                data_type,
                descriptor,
                required,
                kickoff_job,
                Optional([past, future])
            ]
        If it includes past/future date ranges, it should follow the following format:
            - p - pointing
            - h - hourly
            - d - days
            - l - last_processed
            - nd - nearest day
            - np - nearest pointing

            past and future should end with one of these options. Eg.
                ["-3p", "3pm"] means 3 pointing
                ["-3d", "5d"] means 5 days
                ["-2h", "2h"] means 2 hours
                ["1l"] means last processed
                ["6np"] means nearest 6 pointing

        Validation is performed for each field.
        """
        self._validate_source(self.source)
        self._validate_data_type(self.data_type)
        self._validate_descriptor(self.descriptor)
        self._validate_boolean_fields(self.required, self.kickoff_job)
        self._validate_date_range(self.date_range)

    def serialize(self) -> dict[str, Any]:
        """Serialize dependency node to dictionary."""
        return asdict(self)

    @classmethod
    def deserialize(cls, json_object: dict[str, Any]):
        """Deserialize dictionary to dependency node."""
        return cls(**json_object)

    def _validate_boolean_fields(self, required: bool, kickoff_job: bool) -> None:
        """Validate required and kickoff_job are booleans."""
        if not isinstance(required, bool) or not isinstance(kickoff_job, bool):
            raise ValueError("'required' and 'kickoff_job' must be boolean values")

    def _validate_date_range(self, date_range) -> None:
        """Validate date range format if provided."""
        if not date_range:
            return

        if not isinstance(date_range, (list)) or not (1 <= len(date_range) <= 2):
            raise ValueError(
                "Date range must be a list of 1-2 elements [past] or [past, future], "
                f"got {date_range}"
            )

        # Handle both single-element and two-element lists
        past = date_range[0] if len(date_range) > 0 else None
        future = date_range[1] if len(date_range) > 1 else None

        if past is None and future is None:
            return

        is_nearest = past.endswith(NEAREST_OPTIONS) if past else False

        # Validate past if provided
        if is_nearest:
            past_option = "np" if past.endswith("np") else "nd"
            past_int = int(past[:-2]) if past[:-2] else None
        else:
            past_option = past[-1] if past else None
            past_int = int(past[:-1]) if past else None

        # Validate past option and its integer value
        if (past_option not in DATE_RANGE_OPTIONS) or (
            past_option not in NEAREST_OPTIONS and past_int > 0
        ):
            raise ValueError(
                f"Invalid past '{past}'. Must end with "
                f"{DATE_RANGE_OPTIONS} and must be negative."
            )

        # Validate future if provided
        if future is None:
            return
        elif future.endswith(NEAREST_OPTIONS):
            raise ValueError(
                "Nearest need to be in this format, [<int><option>, ]. "
                "Eg. ['6np',] or ['6nd',]"
            )
        else:
            future_option = future[-1] if future else None
            future_int = int(future[:-1]) if future else None

        # Validate future option and integer value
        if (future_option not in DATE_RANGE_OPTIONS) or (future_int < 0):
            raise ValueError(
                f"Invalid future '{future}'. Must end with "
                f"{DATE_RANGE_OPTIONS} and be positive."
            )

    def _validate_source(self, source: str) -> None:
        """Validate source is valid."""
        if source not in self._data_source_validator.valid_source:
            raise ValueError(
                f"Invalid data source '{source}'. "
                f"Valid sources: {self._data_source_validator.valid_source}"
            )

    def _validate_data_type(self, data_type: str) -> None:
        """Validate data type is valid."""
        if data_type not in self._data_type_validator.valid_type:
            raise ValueError(
                f"Invalid data type '{data_type}'. "
                f"Valid types: {self._data_type_validator.valid_type}"
            )

    def _validate_descriptor(self, descriptor: str) -> None:
        """Validate descriptor is a non-empty string."""
        # TODO: validate descriptor once we finalize the descriptor list
        # for each instrument and data type.
        if not isinstance(descriptor, str) or not descriptor.strip():
            raise ValueError(
                f"Descriptor must be a non-empty string, got '{descriptor}'"
            )


@dataclass
class UpstreamDependencyNode(DependencyNode):
    """Upstream dependency node with date range and other fields.

    Extends DependencyNode with fields required for querying upstream
    dependencies from the database, including date range and other
    optional fields that may be used by the dependency resolver or batch starter.
    """

    start_date: datetime = None
    end_date: datetime = None
    # These optional needs to go after required fields
    # to line with dataclass rules.
    reprocessing: bool = False
    repoint: int | None = None

    # check in init funciton that use do pass in start_date and
    # end_date for this node.
    def __post_init__(self):
        """Validate that start_date and end_date are provided."""
        if self.start_date is None or self.end_date is None:
            raise ValueError(
                "start_date and end_date must be provided for UpstreamDependencyNode"
            )


class TriggerEventType:
    """Enum for different trigger event types."""

    SCIENCE_INGESTION = "science_ingestion"
    ANCILLARY_INGESTION = "ancillary_ingestion"
    SPICE_INGESTION = "spice_ingestion"
    CADENCE = "cadence"
    REPROCESSING = "reprocessing"


class ProcessingJobType:
    """Enum for different type of processing jobs passed to batch job."""

    DAILY = "daily"
    POINTING = "pointing"
    CADENCE = "cadence"
    POINTING_ATTITUDE = "pointing_attitude"


def get_cadence_duration(descriptor: str) -> str | None:
    """Get cadence information from a descriptor.

    Cadence jobs are products at data level l2 or l2b whose descriptor contains
    cadence indicators like "1mo", "3mo", "6mo", or "1yr".

    Parameters
    ----------
    descriptor : str
        The descriptor to check for cadence indicators.

    Returns
    -------
    str or None
        The cadence string (e.g., '1mo', '3mo', '6mo', '1yr').
        Returns None if no cadence indicators are found.

    Examples
    --------
    >>> get_cadence_duration("swe-sci-1mo")
    '1mo'
    """
    # For given descriptor, parse cadence.
    cadence = descriptor.rsplit("-", maxsplit=1)[-1]
    if cadence in VALID_CADENCE_STRS:
        return cadence

    return None


def format_upstream_node_input(yaml_dict: dict) -> dict:
    """Convert a YAML upstream dependency dict to DependencyNode keyword arguments.

    YAML upstream entries use ``upstream_source``, ``upstream_data_type``,
    and ``upstream_descriptor`` as keys. This function maps those keys
    to the DependencyNode field names so the result can be unpacked directly
    with ``DependencyNode(**format_upstream_node_input(yaml_dict))``.

    Parameters
    ----------
    yaml_dict : dict
        A dict with keys ``upstream_source``, ``upstream_data_type``,
        ``upstream_descriptor``, and optionally ``required``,
        ``kickoff_job``, ``date_range``.

    Returns
    -------
    dict
        A dict with keys matching DependencyNode field names.
    """
    # Accept only dictionary format
    if not isinstance(yaml_dict, dict):
        raise ValueError(f"Node must be a dict, got {type(yaml_dict).__name__}")

    required_keys = {
        "upstream_source",
        "upstream_data_type",
        "upstream_descriptor",
    }
    if not required_keys.issubset(yaml_dict.keys()):
        raise ValueError(
            f"Node dict must contain keys {required_keys}, got {set(yaml_dict.keys())}"
        )
    return {
        "source": yaml_dict["upstream_source"],
        "data_type": yaml_dict["upstream_data_type"],
        "descriptor": yaml_dict["upstream_descriptor"],
        "required": yaml_dict.get("required", True),
        "kickoff_job": yaml_dict.get("kickoff_job", True),
        "date_range": yaml_dict.get("date_range", None),
    }
