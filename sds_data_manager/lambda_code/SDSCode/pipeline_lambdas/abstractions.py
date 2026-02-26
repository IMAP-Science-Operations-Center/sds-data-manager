"""Common fuctions for pipeline lambdas."""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, Any


@dataclass
class DependencyNode:
    """Shared between batch starter and dependency lambda."""

    source: str
    data_type: str
    product_name: str
    reprocessing: bool = False
    repoint: Optional[int] = None

    def serialize(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def deserialize(cls, json_object: Dict[str, Any]):
        return cls(**json_object)

@dataclass
class UpstreamDependencyNode(DependencyNode):
    start_date: datetime
    end_date: datetime


class EventSourceType:
    """Enum for different event types."""
    SCIENCE_INGESTION = "science_ingestion"
    ANCILLARY_INGESTION = "ancillary_ingestion"
    SPICE_INGESTION = "spice_ingestion"
    CADENCE = "cadence"
    REPROCESSING = "reprocessing"


class ProcesingJobType:
    """Enum for different type of processing jobs passed to batch job."""
    DAILY = "daily"
    POINTING = "pointing"
    CADENCE = "cadence"
    POINTING_ATTITUDE = "pointing_attitude"


class DateRange:
    """Calculate date range for different event types."""

    @staticmethod
    def calculate_date_range(EventSourceType: str) -> list[list[datetime, datetime]]:
        """Calculate the date range for the job based on the event type.
        
        It can be multiple list of start and end dates depending on the event_type.
        """
        # If key exists in the event, create file validator object to extract necessary
        # information from the file name.

        # If cadence event, derive date range from cadence input parameters.

        # If reprocessing event, derive date range from repointing parameter.

        # For example, something like this:
        #     If event from science or ancillary ingestion, will get s3 full path and instrument name.
        #       if data_type is any instrument:
        #         if ENA or glows:
        #             calculate_repoint_date_range()
        #         else:
        #             calculate_daily_date_range()
        #       if ancillary:
        #         calculate_ancillary_date_range()
        #     If event from reprocessing:
        #       calculate_reprocessing_date_range()
        #     If event from cadence:
        #       calculate_cadence_date_range()
        #     If event from spice indexer:
        #       calculate_spice_date_range()
        pass

    def calculate_daily_date_range():
        pass
    def calculate_repoint_date_range():
        pass
    def calculate_ancillary_date_range():
        pass
    def calculate_cadence_date_range():
        pass
    def calculate_spice_date_range():
        # This includes deriving date for all kernels, spin,
        # repoint, thruster and etc.
        pass
    def calculate_past_n_days_date_range(n: int):
        pass
    def calculate_future_n_days_date_range(n: int):
        pass
    def calculate_reprocessing_date_range(repointing: int):
        pass

