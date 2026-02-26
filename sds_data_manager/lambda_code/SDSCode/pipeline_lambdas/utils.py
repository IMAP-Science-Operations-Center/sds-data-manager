"""Common fuctions for pipeline lambdas."""

from datetime import datetime
from typing import Optional


class DependencyNode:
    """This is shared between batch starter and dependency lambda."""

    source: str
    data_type: str
    product_name: str
    start_date: datetime
    end_date: datetime
    reprocessing: bool = False
    repoint: Optional[int] = None

    def __init__(
        self,
        source: str,
        data_type: str,
        product_name: str,
        start_date: datetime,
        end_date: datetime,
        reprocessing: bool = False,
        repoint: Optional[int] = None,
    ):
        self.source = source
        self.data_type = data_type
        self.product_name = product_name
        self.start_date = start_date
        self.end_date = end_date
        self.reprocessing = reprocessing
        self.repoint = repoint

    def serialize(self):
        return {
            "source": self.source,
            "data_type": self.data_type,
            "product_name": self.product_name,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "reprocessing": self.reprocessing,
            "repoint": self.repoint,
        }

    @classmethod
    def deserialize(cls, json_object):
        return cls(
            source=json_object["source"],
            data_type=json_object["data_type"],
            product_name=json_object["product_name"],
            start_date=json_object["start_date"],
            end_date=json_object["end_date"],
            reprocessing=json_object["reprocessing"],
            repoint=json_object.get("repoint", None),
        )

class EventType:
    """Enum for different event types."""
    SCIENCE_INGESTION = "science_ingestion"
    ANCILLARY_INGESTION = "ancillary_ingestion"
    SPICE_INGESTION = "spice_ingestion"
    CADENCE = "cadence"

class DateRange:
    """Calculate date range for different event types."""
    start_date: datetime
    end_date: datetime

    @staticmethod
    def calculate_date_range(event: dict) -> list[list[datetime, datetime]]:
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
