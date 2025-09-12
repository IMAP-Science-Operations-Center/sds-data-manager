"""Reader for the scheduled job config file."""

import re
from collections import defaultdict
from pathlib import Path

from imap_data_access import VALID_DATALEVELS, VALID_INSTRUMENTS

CONFIG_PATH = Path(__file__).parent / "scheduled_jobs_config.csv"


def read_scheduled_job_config() -> dict[str, list[dict]]:
    """Read the scheduled job config.

    Returns
    -------
    A list of job definitions for each unique schedule.
    """
    header = [
        "schedule",
        "instrument",
        "data_level",
        "descriptor",
    ]

    scheduled_jobs_info = defaultdict(list)

    with open(CONFIG_PATH) as f:
        for line in f:
            if len(line) <= 1 or line.startswith("#"):
                # Skip empty lines and comments
                continue

            contents = [item.strip() for item in line.split(",")]
            if len(contents) != 4:
                raise ValueError(
                    f"Each scheduled job should have {header}\nCurrent line: {line}"
                )
            [schedule, instrument, data_level, descriptor] = contents

            if instrument not in VALID_INSTRUMENTS:
                raise ValueError(f"Invalid instrument: {instrument}")
            if data_level not in VALID_DATALEVELS:
                raise ValueError(f"Invalid data level: {data_level}")
            regex = re.compile(r"^cron\(\S* \S* \S* \S* \S* \S*\)$")
            if not regex.match(schedule):
                raise ValueError(f"Invalid schedule expression: {schedule}")

            scheduled_jobs_info[schedule].append(
                {
                    "data_source": instrument,
                    "data_type": data_level,
                    "descriptor": descriptor,
                }
            )

    return scheduled_jobs_info
