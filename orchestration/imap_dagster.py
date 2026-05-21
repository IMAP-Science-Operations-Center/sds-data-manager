from dagster import Definitions

from orchestration import (
    custom_partitions,
    imap_assets,
    pointing_attitude,
    spice,
    spin,
)

defs = Definitions(
    assets=imap_assets.assets,
    sensors=spin.sensors
    + pointing_attitude.sensors
    + custom_partitions.sensors
    + spice.sensors
    + imap_assets.sensors,
)
