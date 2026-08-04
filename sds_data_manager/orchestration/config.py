"""Contains configuration information for Dagster."""

import datetime
from enum import Enum

MISSION_START_TIME = "2025-09-24T00:00:00"
MISSION_END_TIME = "2045-09-24T00:00:00"

VALID_CADENCE_STRS = ["1mo", "3mo", "6mo", "1yr"]

FIRST_MAP_START_DATE = datetime.datetime(2026, 1, 17, tzinfo=datetime.timezone.utc)

sensor_schedules = {
    "l0": 300,
    "l1": 300,
    "l1a": 300,
    "l1b": 300,
    "l1c": 300,
    "l1d": 300,
    "l2": 300,
    "l2a": 300,
    "l2b": 300,
    "l2c": 300,
    "l2d": 300,
    "l3": 300,
    "l3a": 300,
    "l3b": 300,
    "l3c": 300,
    "l3d": 300,
}


class CadenceDays(float, Enum):
    """Enum for a cadence value and the corresponding days."""

    ONE_YEAR = 365.25
    ONE_MONTH = ONE_YEAR / 12
    THREE_MONTHS = ONE_YEAR / 4
    SIX_MONTHS = ONE_YEAR / 2

    @staticmethod
    def valid_cadence_str():
        """Get a list of valid cadence strings."""
        return VALID_CADENCE_STRS

    @classmethod
    def str_lookup(cls, cadence_str: str | None = None):
        """Get a CadenceDays value from a string.

        Parameters
        ----------
        cadence_str : str, optional
            The cadence string (e.g. "1mo", "3mo", "6mo", "1yr"). If not provided,
            the function will return the list of valid cadence strings.

        Returns
        -------
        CadenceDays, dict[str, CadenceDays]
            The corresponding CadenceDays enum value. If cadence_str is None,
            then a dictionary of valid cadence strings and their corresponding
            CadenceDays enum values is returned.

        """
        lookup = {
            "1mo": cls.ONE_MONTH,
            "3mo": cls.THREE_MONTHS,
            "6mo": cls.SIX_MONTHS,
            "1yr": cls.ONE_YEAR,
        }
        if not cadence_str:
            return lookup

        if cadence_str not in cls.valid_cadence_str():
            raise ValueError(
                f"Invalid cadence: {cadence_str}. Valid cadences are:"
                f" {cls.valid_cadence_str()}"
            )
        return lookup[cadence_str]

    def get_first_job_start_date(
        self, as_string: bool = False
    ) -> datetime.datetime | str:
        """Get the first job start date for this cadence.

        Parameters
        ----------
        as_string : bool
            If True, return the date as a string in the format 'YYYYMMDD'.
            Default is False.

        Returns
        -------
        datetime.datetime | str
            The first job start date for this cadence.
        """
        if self.value == CadenceDays.ONE_MONTH.value:
            # 1mo jobs are not map jobs. We want them to start earlier. E.g. IDEX l2b is
            # a 1 month cadence job and the first job should be a month after launch
            start_date = datetime.datetime.fromisoformat(
                MISSION_START_TIME
            ) + datetime.timedelta(days=self.value)
        else:
            # For map jobs, we want the first job to start at the first map start date
            # plus the cadence. E.g.:
            #    - 3 month maps start at FIRST_MAP_START_DATE + 3 months
            #    - 6 month maps start at FIRST_MAP_START_DATE + 6 months
            #    - 1 year maps start at FIRST_MAP_START_DATE + 1 year
            start_date = FIRST_MAP_START_DATE + datetime.timedelta(days=self.value)
        return start_date.strftime("%Y%m%d") if as_string else start_date
