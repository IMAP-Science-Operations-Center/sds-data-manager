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


def calculate_date_range(event_source: TriggerEventType, downstream_node: DependencyNode) -> list[list[datetime, datetime]]:
    """Calculate the date range for the job based on the trigger event type.

    This function/class is triggered by different events.
    1. Event of a new science or ancillary file arrival from indexer lambda.
        Example event:
            {
                "Records": [
                    {
                        "body": '{"detail": '
                        '{"object": {"key": '
                            '"imap_swe_l1b-in-flight-cal_20240101_v001.cdf"}}'
                        "}"
                    }
                ]
            }
    2. Event of a new science reprocessing.
        {
            "queryStringParameters": {
                "reprocessing": True,
                "start_date": <>,
                "end_date": <>,
                "instrument": None, optional,
                "data_level": None, optional,
                "data_descriptor": None, optional,
            }
        }
    3. Event of a new spice file arrival from spice indexer lambda.
            {
            “Source”: “imap.lambda”,
            “DetailType”: “Processed File”,
            “Detail”: {
                “object”: {
                    “key”: “imap/spice/spin/imap_2025_122_2025_122_02.spin.csv”,
                    }
            }
        }
        or
        {
            “Source”: “imap.lambda”,
            “DetailType”: “Processed File”,
            “Detail”: {
                “object”: {
                    “key”: “imap/spice/repoint/imap_2025_122_02.repoint”,
                    “instrument”: “spacecraft”,
                    }
            }
        }

    4. Event of bulk reprocessing of science.
        Example event:
            {
                "queryStringParameters": {
                    "reprocessing": True,
                    "start_date": <>,
                    "end_date": <>,
                    "instrument": None, optional,
                    "data_level": None, optional,
                    "data_descriptor": None, optional,
                }
            }
    5. Event of cadence job.
        Example event:
            {
                "cadence": 1mo, 3mo, 1yr, or 6mo,
                "start_date": <>,
            }
    6. Event of reprocessing cadence job.
        Example event:
            {
                "cadence": 1mo, 3mo, 1yr, or 6mo,
                "start_date": <>,
                "end_date": <>,
                "reprocessing": True
            }
    
    It can be multiple list of start and end dates depending on the event_type.
    """
    # Determine event source type and extract (source, data_type, product_name)
    # For current downstream node, calculate date range based on:
    #   - event_source type (science, ancillary, spice, cadence, reprocessing)
    #   - downstream node's processing job type (daily, pointing, cadence, pointing_attitude, etc.)
    # 
    # Examples:
    #   - ancillary event + daily downstream job → list of (start_date, end_date) for each day
    #   - reprocessing event + daily downstream job → list of (start_date, end_date) for each day in range
    #   - cadence event + cadence downstream job → single (start_date, end_date) for cadence range
    #   - reprocessing event + cadence downstream job → one or more (start_date, end_date) ranges
    #   - science (HI DE) event + L1B goodtimes downstream job → list for 7 nearest repoint files
    #   - science (ENA/GLOWS) event + pointing downstream job → list for date ranges derived using repoint id of input file
    #   - reprocessing event + pointing downstream job → list of date ranges derived for each pointing in date range
    #
    # For each calculated date range, let IMAPJobHandler do these steps in batch_starter_refactor.py:
    #   - Query dependencies
    #   - Determine job version
    #   - Create dependency file
    #   - Submit job
    
    if event_source == TriggerEventType.SCIENCE_INGESTION:
        if downstream_node.data_type in ["ENA", "GLOWS"]:
            return calculate_repoint_date_range()
        else:
            return calculate_daily_date_range()
    elif event_source == TriggerEventType.ANCILLARY_INGESTION:
        return calculate_ancillary_date_range()
    elif event_source == TriggerEventType.SPICE_INGESTION:
        return calculate_spice_date_range()
    elif event_source == TriggerEventType.CADENCE:
        return calculate_cadence_date_range()
    elif event_source == TriggerEventType.REPROCESSING:
        return calculate_reprocessing_date_range(downstream_node.repoint)
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

