"""Common types for pipeline lambdas."""

import datetime
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

import imap_data_access
from dagster import (
    AssetExecutionContext,
    AssetKey,
    DagsterEventType,
    EventRecordsFilter,
)

from sds_data_manager.lambda_code.SDSCode.api_lambdas import spice_metakernel_api
from sds_data_manager.orchestration import dagster_utilities

# Date range validation constants
NEAREST_OPTIONS = ("nd", "np")
DATE_RANGE_OPTIONS = ("p", "h", "d", "l", *NEAREST_OPTIONS)


@dataclass
class DataSource:
    """Valid data sources for dependency tracking.

    Valid data sources include valid instruments names
    from imap_data_access and other data sources related to SPICE.
    """

    @property
    def valid_source(self) -> list[str]:
        """Add data sources.

        Returns
        -------
        list[str]
            list of valid data sources.
        """
        # TODO: import this from imap_data_access once it's defined
        # or transition this class to imap_data_access
        return [
            "spin",
            "repoint",
            "spice",
            *spice_metakernel_api.KernelCollection().file_types,
            *imap_data_access.VALID_INSTRUMENTS,
        ]


def valid_science(data_level) -> bool:
    """Check if data_level is a valid data level.

    Returns
    -------
    bool
        True if the data_level is in VALID_DATALEVELS.
    """
    return data_level in [*imap_data_access.VALID_DATALEVELS]


@dataclass
class DataType:
    """Valid data types for dependency tracking.

    Valid data types include valid data levels from imap_data_access
    and other data types related to SPICE and ancillary data.
    """

    # TODO: transition these class to imap_data_access once it's defined.
    SPICE: str = "spice"
    SPIN: str = "spin"
    REPOINT: str = "repoint"
    ANCILLARY: str = "ancillary"
    COLLECTION: str = "collection"

    @property
    def valid_type(self) -> list[str]:
        """Add data types.

        Returns
        -------
        list[str]
            list of valid data types.
        """
        return [
            self.SPICE,
            self.ANCILLARY,
            self.SPIN,
            self.REPOINT,
            self.COLLECTION,
            *imap_data_access.VALID_DATALEVELS,
        ]


@dataclass
class TimeRange:
    """A date range with optional pointing numbers.

    Stores start and end times as datetime values and provides
    conversion to and from the yyyymmdd string format used in filenames.

    Attributes
    ----------
    start_time : datetime
        Start of the date range.
    end_time : datetime
        End of the date range.
    pointing_number_start : int or None
        Pointing number for the start time, or None if not applicable.
    pointing_number_end : int or None
        Pointing number for the end time, or None if not applicable.
    """

    start_time: datetime.datetime
    end_time: datetime.datetime
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
        start_time = datetime.datetime.strptime(start_time_string, "%Y%m%d")
        end_time = datetime.datetime.strptime(end_time_string, "%Y%m%d")
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
        start_str = self.start_time.strftime("%Y%m%d")
        end_str = self.end_time.strftime("%Y%m%d")
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

    def to_dagster_name(self) -> str:
        """Return the dagster asset name."""
        return (self.source + "_" + self.data_type + "_" + self.descriptor).replace(
            "-", ""
        )

    def to_dagster_asset(self) -> AssetKey:
        """Return the dagster AssetKey."""
        return AssetKey(self.to_dagster_name())


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
            trigger_job,
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
    trigger_job: bool = True
    dependency_query_time_range: list = field(default_factory=list)
    major_version: int = 1

    def __post_init__(self):
        """Validate all fields on construction."""
        super().__post_init__()
        self._validate_boolean_fields(self.required, self.trigger_job)
        self._validate_date_range(self.dependency_query_time_range)

    def serialize(self) -> dict[str, Any]:
        """Serialize dependency node to dictionary."""
        return asdict(self)

    @classmethod
    def deserialize(cls, json_object: dict[str, Any]):
        """Deserialize dictionary to dependency node."""
        return cls(**json_object)

    def _validate_boolean_fields(self, required: bool, trigger_job: bool) -> None:
        """Validate required and trigger_job are booleans."""
        if not isinstance(required, bool) or not isinstance(trigger_job, bool):
            raise ValueError("'required' and 'trigger_job' must be boolean values")

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

        # Nearest is only valid as a single-element list
        if is_nearest and future is not None:
            raise ValueError(
                "Nearest need to be in this format, [<int><option>, ]. "
                "Eg. ['6np',] or ['6nd',]"
            )

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

    def get_all_files_in_time_range(
        self,
        context: AssetExecutionContext,
        start_dt: datetime.datetime,
        end_dt: datetime.datetime,
    ) -> list:
        """Return the metadata of all assets between start_dt and end_dt."""
        metadata = []

        # Fetch a list of all partition keys that have EVER been materialized
        materialized_partitions = context.instance.get_materialized_partitions(
            self.to_dagster_asset()
        )

        if not materialized_partitions:
            context.log.info(
                f"""Not enought information to process. Missing
                    {self.to_dagster_name()}
                    in range {start_dt!s} to {end_dt!s}"""
            )
            return []

        # Loop through the partitions to determine if they span the time range
        for partition in materialized_partitions:
            partition_start, partition_end = (
                dagster_utilities.parse_dates_from_partition_key(partition)
            )

            if not partition_start or not partition_end:
                continue

            # Apply the overlap logic (StartA < EndB and EndA > StartB)
            if partition_start < end_dt and partition_end > start_dt:
                context.log.info(f"This partition matches: {partition}")
                # Fetch the actual materialization record for this overlapping partition
                mat_event = context.instance.get_event_records(
                    event_records_filter=EventRecordsFilter(
                        event_type=DagsterEventType.ASSET_MATERIALIZATION,
                        asset_key=self.to_dagster_asset(),
                        asset_partitions=[partition],
                    ),
                    limit=1,  # The most recent event is returned first
                )
                if mat_event and mat_event[0].asset_materialization:
                    metadata.append(mat_event[0].asset_materialization.metadata)

        return metadata

    def get_all_files_by_repoint_numbers(self, context, target_repoints: list[int]):
        """Return all metadata of materialized assets for the list of repoints."""
        metadata = []
        materialized_partitions = context.instance.get_materialized_partitions(
            self.to_dagster_asset()
        )

        for partition in materialized_partitions:
            parts = partition.split("_")
            if "repoint" in parts[0]:
                pointing_number = int(parts[0][7:])
                if pointing_number in target_repoints:
                    mat_event = context.instance.get_event_records(
                        event_records_filter=EventRecordsFilter(
                            event_type=DagsterEventType.ASSET_MATERIALIZATION,
                            asset_key=self.to_dagster_asset(),
                            asset_partitions=[partition],
                        ),
                        limit=1,  # The most recent event is returned first
                    )
                    if mat_event and mat_event[0].asset_materialization:
                        metadata.append(mat_event[0].asset_materialization.metadata)

        return metadata


@dataclass
class ProcessingJobNode(Node):
    """Representation of an expected processing job.

    This class contains information about the expected settings for a single processing
    job, including inputs, outputs, and the partition to use.


    """

    inputs: list[DependencyNode]
    outputs: list[DependencyNode]
    partition: str
    spice_types: list[str] = None
    triggering_deps: list[DependencyNode] = None  # Dependencies that trigger processing
    science_inputs: list[DependencyNode] = None
    ancillary_inputs: list[DependencyNode] = None
    spice_inputs: list[DependencyNode] = None
    spin_input: DependencyNode = None
    repoint_input: DependencyNode = None

    def __post_init__(self):
        """Consolidate and modify the inputs."""
        all_inputs = []
        triggering_deps = []
        spice_inputs = []
        ancillary_inputs = []
        science_inputs = []
        spice_types = []
        for dep in self.inputs:
            all_inputs.append(dep)
            if dep.source == "repoint":
                self.repoint_input = dep
            elif dep.data_type == "spice":
                spice_inputs.append(dep)
                spice_types.append(dep.source)
            elif dep.data_type == "spin":
                self.spin_input = dep
            elif dep.data_type == "ancillary":
                ancillary_inputs.append(dep)
            else:
                science_inputs.append(dep)
            if dep.trigger_job:
                triggering_deps.append(dep)

        self.inputs = all_inputs
        self.triggering_deps = triggering_deps
        self.spice_inputs = spice_inputs
        self.ancillary_inputs = ancillary_inputs
        self.science_inputs = science_inputs
        self.spice_types = spice_types


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


@dataclass(frozen=True)
class MapWindow:
    """Represent an ENA map cadence window with explicit date range.

    Parameters
    ----------
    cadence: str
        cadence string, e.g. "3mo", "6mo", "1yr"
    start: datetime.datetime
        start datetime for the window, e.g. "2024-01-01T00:00:00"
    end: datetime.datetime
        end datetime for the window, e.g. "2024-04-01T00:00:00"
    """

    cadence: str
    start: datetime.datetime
    end: datetime.datetime

    def to_partition_name(self) -> str:
        """Convert this window to a Dagster partition key string."""
        start_str = self.start.strftime("%Y-%m-%dT%H:%M:%S")
        end_str = self.end.strftime("%Y-%m-%dT%H:%M:%S")
        return f"cadence-{self.cadence}_{start_str}_to_{end_str}"


@dataclass(frozen=True)
class WindowBoundary:
    """A (month, day) boundary point for a map window.

    Parameters
    ----------
    month: int
        Month of the boundary (1-12). Eg. 1 for January, etc.
    day: int
        Day of the month (1-31, depending on month)
    """

    month: int
    day: int

    def to_datetime(self, year: int) -> datetime.datetime:
        """Convert to a datetime for the given year."""
        return datetime.datetime(
            year, self.month, self.day, tzinfo=datetime.timezone.utc
        )


class BaseENAMapPartition:
    """Base class for defining ENA map partition windows.

    This class provides common utilities for map jobs to determine the
    current map window or retrieve all windows for a given cadence. Map
    windows are used to construct Dagster cadence-based partition names.

    It also provides methods to get all windows for a cadence or determine
    the active window based on the current time. These allow the
    Dagster orchestration code to dynamically determine which map
    partition name to use when starting a map job.
    """

    cadence: ClassVar[str]
    # Define the map windows for the cadence using WindowBoundary.
    boundaries: ClassVar[tuple[WindowBoundary, ...]]

    def __init__(self, current_time: datetime.datetime):
        """Initialize the cadence period with the current time."""
        self.current_time = current_time

    def get_windows(self, year: int | None = None) -> list[MapWindow]:
        """Build all configured windows for the cadence type.

        Based on the current time's year, generate cadence windows
        for the cadence. Eg.
            - For 3mo cadence, it will generate 4 windows:
                - Jan 17 to Apr 18
                - Apr 18 to Jul 18
                - Jul 18 to Oct 17
                - Oct 17 to Jan 17 of the next year
            - For 6mo cadence, it will generate 2 windows:
                - Jan 17 to Jul 18
                - Jul 18 to Jan 17 of the next year
            - For 1yr cadence, it will generate 1 window:
                - Jan 17 to Jan 17 of the next year
        """
        windows: list[MapWindow] = []
        year = self.current_time.year if year is None else year

        for i in range(len(self.boundaries) - 1):
            start_boundary = self.boundaries[i]
            end_boundary = self.boundaries[i + 1]

            # Handle year rollover. A window rolls into the next year if the end
            # boundary falls at or before the start boundary within the calendar
            # year (e.g., Oct -> Jan). Equality (e.g., Jan 17 -> Jan 17 for the
            # 1yr cadence) also means "next year", since a window can never be
            # zero-length.
            start_year = year
            start_point = (start_boundary.month, start_boundary.day)
            end_point = (end_boundary.month, end_boundary.day)
            end_year = year if end_point > start_point else year + 1

            windows.append(
                MapWindow(
                    cadence=self.cadence,
                    start=start_boundary.to_datetime(start_year),
                    end=end_boundary.to_datetime(end_year),
                )
            )
        return windows

    def get_windows_since(self, since_time: datetime.datetime) -> list[MapWindow]:
        """Return all windows since the given time through the current year."""
        windows: list[MapWindow] = []
        for year in range(since_time.year, self.current_time.year + 1):
            windows.extend(self.get_windows(year))

        return windows

    def get_current_window(self) -> MapWindow | None:
        """Return the active map window for current_time.

        Active window is defined relative to the current time.
        For example, if current time is June 1, 2025 and cadence
        is 3mo, the active window would be the one that starts
        on Apr 18, 2025 and ends on Jul 18, 2025. Similarly for
        6mo or 1yr cadence.
        """
        for window in self.get_windows():
            if window.start <= self.current_time < window.end:
                return window
        return None


class Map3MoPartition(BaseENAMapPartition):
    """3-month map partitions."""

    cadence: ClassVar[str] = "3mo"
    # These boundaries are used to construct the 3-month maps windows:
    #     First partition can be 91 days(or 92 days on leap year).
    #     "cadence_3mo_{year}-01-17T00:00:00_to_{year}-04-18T00:00:00",
    #     Next two partition are always 91 days.
    #     "cadence_3mo_{year}-04-18T00:00:00_to_{year}-07-18T00:00:00",
    #     "cadence_3mo_{year}-07-18T00:00:00_to_{year}-10-17T00:00:00",
    #     Last partition is always 92 days.
    #     "cadence_3mo_{year}-10-17T00:00:00_to_{year+1}-01-17T00:00:00
    boundaries: ClassVar[tuple[WindowBoundary, ...]] = (
        WindowBoundary(1, 17),  # Jan 17
        WindowBoundary(4, 18),  # Apr 18
        WindowBoundary(7, 18),  # Jul 18
        WindowBoundary(10, 17),  # Oct 17
        WindowBoundary(1, 17),  # Jan 17 (next year - marks end of last window)
    )


class Map6MoPartition(BaseENAMapPartition):
    """6-month map partitions."""

    cadence: ClassVar[str] = "6mo"
    # These boundaries are used to construct the 6-month maps windows:
    #     First partition can be 182 days(or 183 days on leap year).
    #     "cadence_6mo_{year}-01-17T00:00:00_to_{year}-07-18T00:00:00",
    #     Last partition is always 183 days.
    #     "cadence_6mo_{year}-07-18T00:00:00_to_{year+1}-01-17T00:00:00
    boundaries: ClassVar[tuple[WindowBoundary, ...]] = (
        WindowBoundary(1, 17),  # Jan 17
        WindowBoundary(7, 18),  # Jul 18
        WindowBoundary(1, 17),  # Jan 17 (next year)
    )


class Map1YrPartition(BaseENAMapPartition):
    """1-year map partitions."""

    cadence: ClassVar[str] = "1yr"
    # This boundary is used to construct the 1-year map window:
    #     "cadence_1yr_{year}-01-17T00:00:00_to_{year+1}-01-17T00:00:00
    boundaries: ClassVar[tuple[WindowBoundary, ...]] = (
        WindowBoundary(1, 17),  # Jan 17
        WindowBoundary(1, 17),  # Jan 17 (next year)
    )
