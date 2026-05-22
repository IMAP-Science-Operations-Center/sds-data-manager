from dagster import Definitions

from orchestration import (
    custom_partitions,
    imap_assets,
    repoint_file,
    spice,
    spin,
)

defs = Definitions(
    assets=imap_assets.assets,
    sensors=spin.sensors
    + repoint_file.sensors
    + custom_partitions.sensors
    + spice.sensors
    + imap_assets.sensors,
)