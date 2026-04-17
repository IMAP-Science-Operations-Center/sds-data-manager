"""Common functions for pipeline lambdas."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional

from .. import VALID_CADENCE_STRS


@dataclass
class DependencyNode:
    """Shared between batch starter and dependency lambda."""

    source: str
    data_type: str
    descriptor: str
    required: bool = True
    kickoff_job: bool = True
    date_range: list = None

    def serialize(self) -> dict[str, Any]:
        """Serialize dependency node to dictionary."""
        return asdict(self)

    @classmethod
    def deserialize(cls, json_object: dict[str, Any]):
        """Deserialize dictionary to dependency node."""
        return cls(**json_object)


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
    repoint: Optional[int] = None

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
    cadence = descriptor.split("-")[-1]
    if cadence in VALID_CADENCE_STRS:
        return cadence

    return None
