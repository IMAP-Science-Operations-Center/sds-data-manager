"""Stores the dependency configuration for IMAP data products.

We can keep track of dependencies by tracking nodes in a graph. Each node
represents a data product and the edges represent the dependencies between
them. There is an upstream/downstream relationship between nodes. A node
can be any data product, from a science file (instrument, data level, descriptor),
a SPICE file, or an ancillary file.

NOTE: This setup assumes that we get one data file with multiple APIDs data.

This is why we have only one dependency for l0. We expect that we get one
l0 file, eg. imap_idex_l0_raw_20240529_v001.pkts, which contains all the data of all
APIDs. That l0 data file will kick off one l1a process for 'all' as l1a will produce
multiple files with different descriptors (aka different data product per APID). Those
different descriptors are handled through CDF attrs.
"""

import logging
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

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

# Accessed like DEPENDENCIES["HARD"]["UPSTREAM"][node]
DEPENDENCIES = {
    hard_soft: {up_down: defaultdict(list) for up_down in ["UPSTREAM", "DOWNSTREAM"]}
    for hard_soft in ["HARD", "SOFT"]
}

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
        # Instrument, data level, descriptor
        parent_node = tuple(contents[:3])
        child_node = tuple(contents[3:6])
        hard_soft = contents[6]
        # Downstream direction
        DEPENDENCIES[hard_soft]["DOWNSTREAM"][parent_node].append(child_node)
        # Upstream direction (flip parent/child)
        DEPENDENCIES[hard_soft]["UPSTREAM"][child_node].append(parent_node)
