"""Common types for pipeline lambdas."""

from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

import numpy as np
from imap_data_access import ScienceFilePath

from .. import VALID_CADENCE_STRS
from ..dependency import DataSource, DataType

# Date range validation constants
NEAREST_OPTIONS = ("nd", "np")
DATE_RANGE_OPTIONS = ("p", "h", "d", "l", *NEAREST_OPTIONS)


@dataclass
class TimeRange:
    """A date range with optional pointing numbers.

    Stores start and end times as np.datetime64 values and provides
    conversion to and from the yyyymmdd string format used in filenames.

    Attributes
    ----------
    start_time : np.datetime64
        Start of the date range.
    end_time : np.datetime64
        End of the date range.
    pointing_number_start : int or None
        Pointing number for the start time, or None if not applicable.
    pointing_number_end : int or None
        Pointing number for the end time, or None if not applicable.
    """

    start_time: np.datetime64
    end_time: np.datetime64
    pointing_number_start: int | None = None
    pointing_number_end: int | None = None

    @classmethod
    def from_string(
        cls,
        start_time_string: str,
        end_time_string: str,
        pointing_number_start: int | None = None,
        pointing_number_end: int | None = None,
    ) -> "TimeRange":
        """Create a TimeRange from yyyymmdd formatted strings.

        Parameters
        ----------
        start_time_string : str
            Start time in yyyymmdd format (e.g. "20250101").
        end_time_string : str
            End time in yyyymmdd format (e.g. "20250131").
        pointing_number_start : int or None, optional
            Pointing number for the start time.
        pointing_number_end : int or None, optional
            Pointing number for the end time.

        Returns
        -------
        TimeRange
            A TimeRange instance with parsed datetime values.
        """
        start_time = np.datetime64(
            f"{start_time_string[:4]}-{start_time_string[4:6]}-{start_time_string[6:8]}"
        )
        end_time = np.datetime64(
            f"{end_time_string[:4]}-{end_time_string[4:6]}-{end_time_string[6:8]}"
        )
        return cls(
            start_time=start_time,
            end_time=end_time,
            pointing_number_start=pointing_number_start,
            pointing_number_end=pointing_number_end,
        )

    def to_string(self) -> tuple[str, str]:
        """Convert start and end times to yyyymmdd strings.

        Returns
        -------
        tuple[str, str]
            (start_time_string, end_time_string) in yyyymmdd format.
        """
        start_str = str(self.start_time.astype("datetime64[D]")).replace("-", "")
        end_str = str(self.end_time.astype("datetime64[D]")).replace("-", "")
        return start_str, end_str


@dataclass
class Node:
    """Node represents the key pieces of information about the processing starter.

    This contains all the information that is true starting from the input file
    all the way to the output processing job.

    source: Source should be one of DataSource. This represents the instrument or the
    area of responsibility (eg spin, repoint, etc)
    data_type: One of DataType. Represents the level or other more specific information.
    descriptor: String. Additional information which mostly is passed through without
    being used to the next processing step.
    """

    _data_source_validator: ClassVar[DataSource] = DataSource()
    _data_type_validator: ClassVar[DataType] = DataType()

    source: str
    data_type: str
    descriptor: str

    def __post_init__(self):
        """Validate all fields on construction."""
        self._validate_source(self.source)
        self._validate_data_type(self.data_type)
        self._validate_descriptor(self.descriptor)

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
class DependencyNode(Node):
    """Store dependency information for a given Node.

    This does not contain specific time span requirement, it is only relative time spans
    - i.e. rather than specifying that the dependencies cover June 1st to June 5th,
    it is instead 2 days before and 2 days after the processing time.

    A valid DependencyNode must have exactly 5 or 6 elements:
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
            ["-3p", "3p"] means 3 pointing
            ["-3d", "5d"] means 5 days
            ["-2h", "2h"] means 2 hours
            ["-1l"] means last processed
            ["6np"] means nearest 6 pointing

    Validation is performed for each field.

    This information is retrieved from configuration files and used to assemble the
    ProcessingInputCollection for job starting.
    """

    required: bool = True
    kickoff_job: bool = True
    relative_time_range: list = field(default_factory=list)

    def __post_init__(self):
        """Validate all fields on construction."""
        self._validate_boolean_fields(self.required, self.kickoff_job)
        self._validate_date_range(self.relative_time_range)

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

        if not isinstance(date_range, list) or not (1 <= len(date_range) <= 2):
            raise ValueError(
                "Date range must be a list of 1-2 elements [past] or [past, future], "
                f"got {date_range}"
            )

        # Handle both single-element and two-element lists
        past = date_range[0]
        future = date_range[1] if len(date_range) > 1 else None

        is_nearest = past.endswith(NEAREST_OPTIONS)

        # Validate past
        if is_nearest:
            past_option = "np" if past.endswith("np") else "nd"
            past_int = int(past[:-2])
        else:
            past_option = past[-1]
            past_int = int(past[:-1])

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
            return True
        elif future.endswith(NEAREST_OPTIONS):
            raise ValueError(
                "Nearest need to be in this format, [<int><option>, ]. "
                "Eg. ['6np',] or ['6nd',]"
            )
        else:
            future_option = future[-1]
            future_int = int(future[:-1])

        # Validate future option and integer value
        if (future_option not in DATE_RANGE_OPTIONS) or (future_int < 0):
            raise ValueError(
                f"Invalid future '{future}'. Must end with "
                f"{DATE_RANGE_OPTIONS} and be positive."
            )


@dataclass
class ProcessingJobNode(Node):
    """Representation of an expected processing job.

    This class contains information about the expected settings for a single processing
    job, such as the time range.
    This class should be used whenever a specific processing job is needed.

    Attributes
    ----------
    time_span: TimeRange
        The time span of the data - this should include the time span for one processing
        job. Nominally, this is one day or one repointing (if repointing is included.)


    """

    time_span: TimeRange
    reprocessing: bool = False

    def convert_to_cli_call(self, version) -> str:
        """Convert the node to a string for input into imap_processing CLI.

        The time values from start_time and pointing_number_start are passed forward,
        with the end_time and pointing_number_end dropped.
        """
        validate_expected_output = ScienceFilePath.generate_from_inputs(
            self.source,
            self.data_type,
            self.descriptor,
            self.time_span.start_time,
            version,
            self.time_span.pointing_number_start,
        ).validate_filename()

        if validate_expected_output != "":
            raise ValueError(
                "Invalid input to IMAP CLI. Would produce file with "
                "the following errors: {validate_expected_output}"
            )

        output = (
            f"--instrument {self.instrument} --data-level {self.data_level} "
            f"--descriptor {self.descriptor} --start-date {self.start_date}"
            f"--version {self.version} --dependency {self.dependency}"
        )
        if self.repoint:
            output += f"--repointing {self.repoint}"
        if self.upload_to_sdc:
            output += "--upload-to-sdc"

        return output


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


def format_upstream_node_input(yaml_dict: dict) -> DependencyNode:
    """Convert a YAML upstream dependency dict to a DependencyNode.

    YAML upstream entries use ``upstream_source``, ``upstream_data_type``,
    and ``upstream_descriptor`` as keys. This function maps those keys
    to the DependencyNode field names and constructs the node directly.

    Parameters
    ----------
    yaml_dict : dict
        A dict with keys ``upstream_source``, ``upstream_data_type``,
        ``upstream_descriptor``, and optionally ``required``,
        ``kickoff_job``, ``date_range``.

    Returns
    -------
    DependencyNode
        A fully validated DependencyNode instance.
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
    return DependencyNode(
        source=yaml_dict["upstream_source"],
        data_type=yaml_dict["upstream_data_type"],
        descriptor=yaml_dict["upstream_descriptor"],
        required=yaml_dict.get("required", True),
        kickoff_job=yaml_dict.get("kickoff_job", True),
        relative_time_range=yaml_dict.get("date_range", None),
    )
