"""Configuration for monitoring science data freshness."""

from dataclasses import dataclass


@dataclass
class Alarm:
    """An alarm that fires if no new data arrives within threshold_days."""

    instrument: str
    data_level: str
    descriptor: str
    # How many days since the last file before the alarm triggers
    threshold_days: int
    # Set to True for products where each file covers multiple days
    # (e.g. IDEX sci-10days). The newest file's start_date stays the same
    # until the next multi-day period begins, so its age can't be used to
    # detect a gap. Instead, the alarm checks how long ago the newest file was
    # last ingested, since it is re-ingested each time new data arrives for it.
    # Leave False for daily products: they are checked on the age of the newest
    # start_date, so a new version of an old day can't hide a missing new day.
    multi_day_files: bool = False

    def __post_init__(self):
        """Validate the threshold_days value."""
        if self.threshold_days <= 0:
            raise ValueError("threshold_days must be greater than 0.")

    def name(self):
        """Return the name of the alarm."""
        return f"{self.instrument}_{self.data_level}_{self.descriptor}"

    def alarm_description(self):
        """Return the description of the alarm."""
        product = f"{self.instrument.upper()}, {self.data_level}, {self.descriptor}"
        if self.multi_day_files:
            return (
                f"{product} data is stale: the newest file has not been updated "
                f"in the last {self.threshold_days} days."
            )
        return (
            f"{product} data is stale: no data with a new start date in the "
            f"last {self.threshold_days} days."
        )


MONITORED_SCIENCE_PRODUCTS = [Alarm("idex", "l0", "raw", 5)] + [
    Alarm("idex", level, "sci-10days", 8, multi_day_files=True)
    for level in ["l1a", "l1b", "l2a", "l2b"]
]
