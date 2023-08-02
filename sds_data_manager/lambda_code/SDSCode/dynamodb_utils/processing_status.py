from enum import Enum


class ProcessingStatus(Enum):
    """
    Enum for the processing status.
    """

    PENDING = 0
    IN_PROGRESS = 1
    COMPLETED = 2
    FAILED = 3
    CANCELLED = 4
