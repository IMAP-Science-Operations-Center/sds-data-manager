"""Stores the downstream and upstream dependency configuration of some IMAP instruments.

This is used to populate pre-processing dependency table in the database.

NOTE: This setup assumes that we get one data file with multiple APIDs data.
This is why we have only one dependency for l0. We expect that we get one
l0 file, eg. imap_idex_l0_raw_20240529_v001.pkts, which contains all the data of all
APIDs. That l0 data file will kick off one l1a process for 'all' as l1a will produce
multiple files with different descriptor(aka different data product per APID). Those
different descriptor are handled by CDF attrs.
"""

import logging
from pathlib import Path

from .database.models import PreProcessingDependency

logger = logging.getLogger(__name__)

downstream_dependents = []
header = [
    "primary_instrument",
    "primary_data_level",
    "primary_descriptor",
    "dependent_instrument",
    "dependent_data_level",
    "dependent_descriptor",
    "relationship",
    "direction",
]

with open(Path(__file__).parent / "dependency_config.csv") as f:
    for line in f:
        # NOTE: remove this ',,,,,,,' if you edited the csv file in excel,
        # it will add this line
        if len(line) <= 1 or line.startswith("#"):
            # Skip empty lines and comments
            continue
        contents = line.strip().replace(", ", ",").split(",")
        if len(contents) != 8:
            raise ValueError(f"Each dependency must have 8 items\nCurrent line: {line}")

        logger.info(contents)
        dependency = PreProcessingDependency(**{h: c for h, c in zip(header, contents)})
        downstream_dependents.append(dependency)

upstream_dependents = [dep.reverse_direction() for dep in downstream_dependents]

all_dependents = downstream_dependents + upstream_dependents
